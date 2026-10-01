// SPDX-License-Identifier: Apache-2.0
`default_nettype none

// Fixed-function UART transmitter: 8 data bits, no parity, one stop bit.
// start is accepted only while idle. Busy requests are ignored.
module uart_tx #(
    parameter integer CLKS_PER_BIT = 434
) (
    input wire clk,
    input wire rst_n,
    input wire start,
    input wire [7:0] data,
    output wire tx,
    output reg busy
);
    localparam integer COUNT_WIDTH = (CLKS_PER_BIT > 1) ? $clog2(CLKS_PER_BIT) : 1;
    reg [COUNT_WIDTH-1:0] count;
    reg [3:0] bit_index;
    reg [9:0] frame;
    assign tx = busy ? frame[0] : 1'b1;

    always @(posedge clk) begin
        if (!rst_n) begin
            busy <= 1'b0;
            count <= 0;
            bit_index <= 0;
            frame <= 10'h3ff;
        end else if (!busy) begin
            if (start) begin
                frame <= {1'b1, data, 1'b0};
                busy <= 1'b1;
                count <= CLKS_PER_BIT - 1;
                bit_index <= 0;
            end
        end else if (count != 0) begin
            count <= count - 1'b1;
        end else if (bit_index == 9) begin
            busy <= 1'b0;
        end else begin
            frame <= {1'b1, frame[9:1]};
            bit_index <= bit_index + 1'b1;
            count <= CLKS_PER_BIT - 1;
        end
    end
endmodule

`default_nettype wire
