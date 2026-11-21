`timescale 1ns/1ps
module rx_retune_tb;
   reg clk = 0;
   always #5 clk = !clk;
   reg reset = 1;
   reg [31:0] command = 0;
   reg command_valid = 0;
   reg miso = 0;
   reg calibration_start = 0;
   wire busy;
   wire command_ready, spi_ready, strobe, result_valid;
   wire [7:0] address;
   wire [31:0] data, readback, result;
   integer writes;
   reg [23:0] first_word, last_word;

   rx_retune #(.CAL_TIMEOUT_CYCLES(24'd5000)) dut
     (.clk(clk), .reset(reset), .calibration_start(calibration_start), .command(command), .command_valid(command_valid),
      .command_ready(command_ready), .spi_ready(spi_ready), .spi_readback(readback[7:0]),
      .busy(busy), .spi_strobe(strobe), .spi_address(address), .spi_data(data),
      .result(result), .result_valid(result_valid), .result_ready(1'b1));
   simple_spi_core #(.BASE(8)) spi
     (.clock(clk), .reset(reset), .set_stb(strobe || calibration_start),
      .set_addr(calibration_start ? 8'd10 : address),
      .set_data(calibration_start ? 32'h80160200 : data),
      .readback(readback), .readback_stb(), .ready(spi_ready), .sen(), .sclk(),
      .mosi(), .miso(miso), .debug());

   always @(posedge clk) begin
      if (strobe && address == 10) begin
         if (writes == 0) first_word <= data[31:8];
         last_word <= data[31:8];
         writes <= writes + 1;
      end
   end

   task run(input [31:0] request, input input_bit, input [23:0] expected_first,
            input [23:0] expected_last, input integer expected_writes, input expected_failure);
      begin
         @(negedge clk);
         while (!command_ready) @(negedge clk);
         writes = 0;
         miso = input_bit;
         command = request;
         command_valid = 1;
         @(negedge clk);
         command_valid = 0;
         while (!result_valid) @(negedge clk);
         if (writes != expected_writes ||
             (expected_writes != 0 && (first_word !== expected_first || last_word !== expected_last)))
           $fatal(1, "unexpected SPI sequence (%h, %h, %d)", first_word, last_word, writes);
         if (result[31:25] !== request[31:25] || result[24] !== expected_failure)
           $fatal(1, "unexpected tagged status: %h", result);
         if (request[24:20] == 5'h10 && result[15:0] < 5000)
           $fatal(1, "PLL read completed before the guard time");
      end
   endtask

   task stage_full_profile;
      integer pair;
      begin
         for (pair=0; pair<8; pair=pair+1) begin
            @(negedge clk);
            while (!command_ready) @(negedge clk);
            command = 32'h0110ffff | (pair << 16);
            command_valid = 1;
            @(negedge clk);
            command_valid = 0;
         end
      end
   endtask

   task calibration_test(input expected_timeout);
      begin
         @(negedge clk);
         while (!command_ready) @(negedge clk);
         writes = 0;
         miso = 1;
         calibration_start = 1;
         @(negedge clk);
         calibration_start = 0;
         if (!busy) $fatal(1, "normal control path was not held during RF DC calibration");
         if (!expected_timeout) begin
            wait (writes == 3);
            @(negedge clk);
            miso = 0;
         end
         while (!result_valid) begin
            @(negedge clk);
            if (!busy) $fatal(1, "normal control path was released early");
         end
         if (writes < 3 || first_word !== 24'h001600 || last_word !== 24'h001600)
           $fatal(1, "wrong calibration poll SPI transaction");
         if (result[31:25] !== 0 || result[24] !== expected_timeout ||
             (result[23:0] >= 5000) !== expected_timeout)
           $fatal(1, "unexpected calibration telemetry: %h", result);
      end
   endtask

   initial begin
      repeat (3) @(negedge clk);
      reset = 0;
      run(32'h02003700, 1, 24'h003700, 24'h003700, 1, 0);
      run(32'h05000000, 1, 24'h825a01, 24'h024700, 2, 0);
      run(32'h07000001, 1, 24'h825a21, 24'h024700, 2, 0);
      run(32'h09000001, 0, 24'h825a21, 24'h024700, 64, 1);
      stage_full_profile();
      run(32'h0b200007, 1, 24'h825c70, 24'h025e00, 81, 0);
      run(32'h0d000007, 1, 24'h825ae1, 24'h024700, 2, 0);
      stage_full_profile();
      run(32'h0f200000, 0, 24'h825c00, 24'h025e00, 81, 1);
      // Polling is opt-in. A stock trigger still goes through SPI on reset.
      @(negedge clk);
      calibration_start = 1;
      @(negedge clk);
      calibration_start = 0;
      if (busy) $fatal(1, "RF DC polling started without being enabled");
      run(32'h11300001, 0, 0, 0, 0, 0);
      calibration_test(0);
      calibration_test(1);
      run(32'h13300000, 0, 0, 0, 0, 0);
      $display("Fast Lock recall/refill, lock timeout, RF DC polling and timeout passed");
      $finish;
   end

   initial #2000000 $fatal(1, "test timed out");
endmodule
