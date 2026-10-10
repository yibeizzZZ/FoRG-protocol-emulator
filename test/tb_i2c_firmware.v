`default_nettype none
`timescale 1ns / 1ps
// Test-only wired-AND bus. A HIGH requires every participant to release.
// Optional threshold propagation delays bound digital timing; these are not RC SPICE models.
`ifndef I2C_RISE_NS
`define I2C_RISE_NS 0
`endif
`ifndef I2C_SCL_FALL_NS
`define I2C_SCL_FALL_NS 0
`endif
`ifndef I2C_SDA_FALL_NS
`define I2C_SDA_FALL_NS 0
`endif
module tb_i2c_firmware;
  reg clk, rst_n, ena;
  reg [7:0] ui_in, host_data, noise;
  reg slave_sda_low, slave_scl_low;
  wire [7:0] uo_out, uio_out, uio_oe;
  tri1 raw_sda, raw_scl;
  wire sda, scl;
  assign raw_sda = uio_oe[0] ? uio_out[0] : 1'bz;
  assign raw_scl = uio_oe[1] ? uio_out[1] : 1'bz;
  assign raw_sda = slave_sda_low ? 1'b0 : 1'bz;
  assign raw_scl = slave_scl_low ? 1'b0 : 1'bz;
  assign #(`I2C_RISE_NS, `I2C_SDA_FALL_NS) sda = raw_sda;
  assign #(`I2C_RISE_NS, `I2C_SCL_FALL_NS) scl = raw_scl;
  // Models external host/bus isolation during programming; M2 has no such mux.
`ifdef I2C_UI_HOST
  // Full master uses UI-only host packets, with the physical bus always attached.
  wire [7:0] inputs = {noise[7:2], scl, sda};
`else
  wire [7:0] inputs = ui_in[7] ? {noise[7:2], scl, sda} : host_data;
`endif
  tt_um_forg_protocol_engine user_project (
      .ui_in(ui_in), .uo_out(uo_out), .uio_in(inputs),
      .uio_out(uio_out), .uio_oe(uio_oe),
      .ena(ena), .clk(clk), .rst_n(rst_n)
  );
  initial begin
    $dumpfile("i2c_firmware.fst");
    $dumpvars(0, tb_i2c_firmware);
  end
endmodule
