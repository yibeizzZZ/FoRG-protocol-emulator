/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

// SPDX-License-Identifier: Apache-2.0
// Educational example: an 8-byte programmable output engine.
`default_nettype none

module tt_um_forg_protocol_emulator (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    // Host interface (all inputs must be synchronous to clk):
    // ui_in[7]   = RUN: 0 loads/rewinds, 1 executes.
    // ui_in[6]   = WRITE: write one byte on each rising edge while RUN=0.
    // ui_in[2:0] = instruction address, 0..7.
    // uio_in     = instruction byte to write.
    reg [7:0] program_mem [0:7];
    reg [2:0] pc;
    reg [3:0] wait_count;
    reg       pin_value;
    wire [7:0] instruction = program_mem[pc];

    assign uo_out  = {7'b0, pin_value};
    assign uio_out = 8'b0;
    assign uio_oe  = 8'b0;  // All bidirectional pins are inputs here.

    always @(posedge clk) begin
        if (!rst_n) begin
            pc         <= 3'd0;
            wait_count <= 4'd0;
            pin_value  <= 1'b0;
            // Program memory is NOT reset. Load it before the first run.
        end else if (ena) begin
            if (!ui_in[7]) begin
                // Loading stops execution and rewinds the core.
                pc         <= 3'd0;
                wait_count <= 4'd0;
                pin_value  <= 1'b0;
                if (ui_in[6])
                    program_mem[ui_in[2:0]] <= uio_in;
            end else if (wait_count != 0) begin
                wait_count <= wait_count - 4'd1;
            end else begin
                pc <= pc + 3'd1;
                case (instruction[7:4])
                    4'h0: pin_value  <= instruction[0];   // SET 0 or 1
                    4'h1: wait_count <= instruction[3:0]; // WAIT 0..15
                    4'h2: pc         <= instruction[2:0]; // JMP 0..7
                    default: begin end                   // Other opcodes: NOP
                endcase
            end
        end
    end

    wire _unused = &{ui_in[5:3], 1'b0};
endmodule
