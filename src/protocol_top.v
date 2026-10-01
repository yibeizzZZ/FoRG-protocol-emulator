// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module tt_um_forg_protocol_engine (
    input wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input wire ena, clk, rst_n
);
    protocol_engine engine (
        .clk(clk), .rst_n(rst_n), .ena(ena), .control(ui_in),
        .pins_in(uio_in), .pins_out(uio_out), .pins_oe(uio_oe), .readback(uo_out)
    );
endmodule
`default_nettype wire
