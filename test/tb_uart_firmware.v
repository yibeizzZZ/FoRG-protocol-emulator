// SPDX-License-Identifier: Apache-2.0
`default_nettype none
`timescale 1ns / 1ps

module tb_uart_firmware;
    reg clk, rst_n, ena;
    reg [7:0] ui_in, uio_in;
    wire [7:0] uo_out, uio_out, uio_oe;

    tt_um_forg_protocol_engine user_project (
        .clk(clk), .rst_n(rst_n), .ena(ena),
        .ui_in(ui_in), .uio_in(uio_in),
        .uo_out(uo_out), .uio_out(uio_out), .uio_oe(uio_oe)
    );

    // Independent comparison only. No reference signal feeds the M2 engine.
    reg ref_rst_n, ref_start;
    reg [7:0] ref_data;
    wire ref_tx, ref_busy;
    uart_tx #(.CLKS_PER_BIT(434)) reference_uart (
        .clk(clk), .rst_n(ref_rst_n), .start(ref_start), .data(ref_data),
        .tx(ref_tx), .busy(ref_busy)
    );

    initial begin
        $dumpfile("uart_firmware.fst");
        $dumpvars(0, tb_uart_firmware);
    end
endmodule
`default_nettype wire
