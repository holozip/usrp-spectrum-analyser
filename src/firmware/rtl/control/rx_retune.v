// RX Fast Lock recall and verified profile refill. The host serializes requests.
module rx_retune
  #(parameter CAL_TIMEOUT_CYCLES = 24'd5000000)
  (
   input clk, input reset, input calibration_start,
   input [31:0] command, input command_valid, output command_ready,
   input spi_ready, input [7:0] spi_readback,
   output busy, output spi_strobe, output [7:0] spi_address, output [31:0] spi_data,
   output reg [31:0] result, output result_valid, input result_ready
   );

   localparam IDLE=0, DIVIDER=1, CONFIG=2, TRANSFER=3, WAIT_SPI=4,
              SETTLE=5, RESULT=6, CAL_START=7, CAL_POLL=8, CAL_READ=9;
   localparam ADDRESS=0, DATA=1, WRITE=2, READ_ADDRESS=3, READ=4, STOP=5;
   reg [3:0] state;
   reg calibration_polling;
   reg [23:0] calibration_cycles;
   reg [6:0] tag;
   reg recall, checking_lock, refill, load_failed;
   reg [2:0] slot, stage;
   reg [3:0] index;
   reg [15:0] uploaded;
   reg [7:0] profile [0:15];
   wire [3:0] upload_index = {command[18:16], 1'b0};
   reg [23:0] word;
   reg [6:0] polls;
   reg [12:0] settle;
   reg [15:0] cycles;

   assign busy = state != IDLE;
   assign command_ready = state == IDLE && spi_ready;
   assign spi_strobe = state == DIVIDER || state == CONFIG || state == TRANSFER || state == CAL_POLL;
   assign spi_address = state == DIVIDER ? 8'd8 : state == CONFIG ? 8'd9 : 8'd10;
   assign spi_data = state == DIVIDER ? 32'd4 : state == CONFIG ? 32'h98000001 :
                     state == CAL_POLL ? 32'h00160000 : {word, 8'b0};
   assign result_valid = state == RESULT;

   always @(posedge clk) begin
      if (reset) begin
         state <= IDLE;
         result <= 0;
         uploaded <= 0;
         calibration_polling <= 0;
      end else begin
         if (busy && state != RESULT)
           cycles <= cycles + 1'b1;
         if (state == CAL_POLL || state == CAL_READ)
           calibration_cycles <= calibration_cycles + 1'b1;
         case (state)
           IDLE: if (calibration_start && calibration_polling) begin
              state <= CAL_START;
           end else if (command_valid && command_ready) begin
              if (command[24:20] == 5'h13) begin
                 calibration_polling <= command[0];
                 result <= {command[31:25], 24'b0, command[0]};
                 state <= RESULT;
              end else if (command[24:20] == 5'h11) begin
                 profile[upload_index] <= command[7:0];
                 profile[upload_index | 4'b1] <= command[15:8];
                 uploaded[upload_index] <= 1;
                 uploaded[upload_index | 4'b1] <= 1;
              end else begin
                 tag <= command[31:25];
                 recall <= command[24:20] == 5'h10;
                 refill <= command[24:20] == 5'h12;
                 load_failed <= 0;
                 slot <= command[2:0];
                 stage <= ADDRESS;
                 index <= 0;
                 case (command[24:20])
                   5'h10: word <= {16'h825a, command[2:0], 4'b0, 1'b1};
                   5'h12: word <= {16'h825c, 1'b0, command[2:0], 4'b0};
                   default: word <= command[23:0];
                 endcase
                 polls <= 0;
                 checking_lock <= 0;
                 cycles <= 0;
                 if (command[24:20] == 5'h12 && !(&uploaded)) begin
                    result <= {command[31:25], 1'b1, 24'b0};
                    state <= RESULT;
                 end else begin
                    if (command[24:20] == 5'h12) uploaded <= 0;
                    state <= DIVIDER;
                 end
              end
           end
           DIVIDER: state <= CONFIG;
           CONFIG: state <= TRANSFER;
           TRANSFER: state <= WAIT_SPI;
           WAIT_SPI: if (spi_ready) begin
              if (refill) begin
                 state <= TRANSFER;
                 case (stage)
                   ADDRESS: begin
                      word <= {16'h825d, profile[index]};
                      stage <= DATA;
                   end
                   DATA: begin
                      word <= 24'h825f03;
                      stage <= WRITE;
                   end
                   WRITE: if (index == 15) begin
                      word <= 24'h825f00;
                      stage <= STOP;
                   end else begin
                      index <= index + 1'b1;
                      word <= {16'h825c, 1'b0, slot, (index + 4'b1)};
                      stage <= ADDRESS;
                   end
                   STOP: begin
                      index <= 0;
                      word <= {16'h825c, 1'b0, slot, 4'b0};
                      stage <= READ_ADDRESS;
                   end
                   READ_ADDRESS: begin
                      word <= 24'h025e00;
                      stage <= READ;
                   end
                   READ: begin
                      if (spi_readback != profile[index]) load_failed <= 1;
                      if (index == 15) begin
                         result <= {tag, (load_failed || spi_readback != profile[index]), index, 4'b0, cycles};
                         state <= RESULT;
                      end else begin
                         index <= index + 1'b1;
                         word <= {16'h825c, 1'b0, slot, (index + 4'b1)};
                         stage <= READ_ADDRESS;
                      end
                   end
                   default: state <= IDLE;
                 endcase
              end else if (!recall || (checking_lock && (spi_readback[1] || polls == 63))) begin
                 result <= {tag, recall && !spi_readback[1], spi_readback, cycles};
                 state <= RESULT;
              end else begin
                 // The initial guard allows the detector to reflect the new LO.
                 // GPIF runs at 100 MHz: 50 us before the first status read.
                 settle <= checking_lock ? 13'd399 : 13'd4999;
                 state <= SETTLE;
              end
           end
           SETTLE: if (settle == 0) begin
              checking_lock <= 1;
              polls <= polls + 1'b1;
              word <= 24'h024700;
              state <= TRANSFER;
           end else settle <= settle - 1'b1;
           // Let the UHD calibration-start write finish, then keep the normal
           // SPI port blocked while reading the same RF DC busy bit as UHD.
           CAL_START: if (spi_ready) begin
              calibration_cycles <= 0;
              state <= CAL_POLL;
           end
           CAL_POLL: state <= CAL_READ;
           CAL_READ: if (spi_ready) begin
              if (!spi_readback[1] || calibration_cycles >= CAL_TIMEOUT_CYCLES) begin
                 // Tag zero is reserved for unsolicited calibration telemetry.
                 result <= {7'b0, spi_readback[1], calibration_cycles};
                 state <= RESULT;
              end else state <= CAL_POLL;
           end
           RESULT: if (result_ready) state <= IDLE;
           default: state <= IDLE;
         endcase
      end
   end
endmodule
