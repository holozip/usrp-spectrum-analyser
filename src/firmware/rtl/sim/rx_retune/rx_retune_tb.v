`timescale 1ns/1ps
module rx_retune_tb;
   reg clk = 0;
   always #5 clk = !clk;
   reg reset = 1;
   reg [31:0] command = 0;
   reg command_valid = 0;
   reg miso = 0;
   reg calibration_start = 0;
   reg codec_reset = 0;
   reg codec_write = 0;
   reg [31:0] host_word = 0;
   wire unified_gain;
   wire [31:0] rewritten_word;
   wire busy;
   wire command_ready, spi_ready, strobe, result_valid;
   wire [7:0] address;
   wire [31:0] data, readback, result;
   integer writes;
   reg [23:0] first_word, last_word;

   rx_retune #(.CAL_TIMEOUT_CYCLES(24'd5000)) dut
     (.clk(clk), .reset(reset), .calibration_start(calibration_start),
      .codec_reset(codec_reset), .unified_gain(unified_gain), .command(command), .command_valid(command_valid),
      .command_ready(command_ready), .spi_ready(spi_ready), .spi_readback(readback[7:0]),
      .busy(busy), .spi_strobe(strobe), .spi_address(address), .spi_data(data),
      .result(result), .result_valid(result_valid), .result_ready(1'b1));
   simple_spi_core #(.BASE(8)) spi
     (.clock(clk), .reset(reset), .set_stb(strobe || calibration_start),
      .set_addr(calibration_start ? 8'd10 : address),
      .set_data(calibration_start ? 32'h80160200 : data),
      .readback(readback), .readback_stb(), .ready(spi_ready), .sen(), .sclk(),
      .mosi(), .miso(miso), .debug());

   unified_rx_gain gain_table
     (.clk(clk), .reset(reset), .enabled(unified_gain), .codec_write(codec_write),
      .host_word(host_word), .spi_word(rewritten_word));

   task codec_word(input [31:0] original_word, input [31:0] expected_word);
      begin
         @(negedge clk);
         codec_write = 1;
         host_word = original_word;
         #1;
         if (rewritten_word !== expected_word)
           $fatal(1, "unexpected codec SPI word: %h, wanted %h", rewritten_word, expected_word);
         @(negedge clk);
         codec_write = 0;
      end
   endtask

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
      codec_word(32'h81302300, 32'h81302300);
      codec_word(32'h81310400, 32'h81310400);
      run(32'h15400001, 0, 0, 0, 0, 0);
      if (!unified_gain || result[23:16] !== 8'ha5) $fatal(1, "gain mode did not enable");
      if (rewritten_word !== host_word) $fatal(1, "rewrote a non-codec SPI transaction");
      codec_word(32'h81310400, 32'h81312400);
      codec_word(32'h81322c00, 32'h81322100);
      codec_word(32'h81330000, 32'h81332000);
      codec_word(32'h81371e00, 32'h81371e00);
      codec_word(32'h01310000, 32'h01310000);
      codec_word(32'h81304d00, 32'h81304d00);
      codec_word(32'h8131ff00, 32'h81310000);
      @(negedge clk);
      codec_reset = 1;
      @(negedge clk);
      codec_reset = 0;
      if (unified_gain) $fatal(1, "codec reset did not clear unified mode");
      codec_word(32'h8131ff00, 32'h8131ff00);
      run(32'h17400000, 0, 0, 0, 0, 0);
      if (unified_gain || result[23:16] !== 8'ha4) $fatal(1, "gain mode did not disable");
      $display("Fast Lock, RF DC polling, and unified gain SPI substitution passed");
      $finish;
   end

   initial #2000000 $fatal(1, "test timed out");
endmodule
