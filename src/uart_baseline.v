// SPDX-License-Identifier: Apache-2.0
`default_nettype none
`ifndef UART_CLKS_PER_BIT
`define UART_CLKS_PER_BIT 434
`endif

module tt_um_forg_uart_baseline (
    input wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input wire ena, clk, rst_n
);
    wire tx, busy;
    uart_tx #(.CLKS_PER_BIT(`UART_CLKS_PER_BIT)) transmitter (
        .clk(clk), .rst_n(rst_n), .start(ena && uio_in[0]),
        .data(ui_in), .tx(tx), .busy(busy)
    );
    assign uo_out = {6'b0, busy, tx};
    assign uio_out = 8'b0;
    assign uio_oe = 8'b0;
    wire unused = &{uio_in[7:1], 1'b0};
endmodule
`default_nettype wire
