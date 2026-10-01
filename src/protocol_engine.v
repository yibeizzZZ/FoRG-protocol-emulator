// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module protocol_engine (
    input wire clk, rst_n, ena,
    input wire [7:0] control,
    input wire [7:0] pins_in,
    output reg [7:0] pins_out,
    output wire [7:0] pins_oe,
    output wire [7:0] readback
);
    reg [15:0] program_mem [0:31];
    reg [31:0] valid_low, valid_high;
    reg [7:0] registers [0:3];
    reg [4:0] pc;
    reg [7:0] wait_count, direction;
    reg halted, fault;
    wire run = control[7];
    wire [15:0] instruction = program_mem[pc];
    wire [3:0] opcode = instruction[15:12];
    wire [1:0] rd = instruction[11:10];
    wire [1:0] rs = instruction[9:8];
    wire [7:0] immediate = instruction[7:0];
    wire ready = valid_low[pc] && valid_high[pc];
    integer i;

    assign pins_oe = (run && rst_n && !halted && !fault) ? direction : 8'b0;
    assign readback = control[6:5] == 2'b00 ? pins_out :
                      control[6:5] == 2'b01 ? {fault, halted, (wait_count != 0), pc} :
                      control[6:5] == 2'b10 ? registers[control[1:0]] : direction;

    always @(posedge clk) begin
        if (!rst_n) begin
            valid_low <= 0;
            valid_high <= 0;
            pc <= 0;
            wait_count <= 0;
            pins_out <= 0;
            direction <= 0;
            halted <= 0;
            fault <= 0;
            for (i = 0; i < 4; i = i + 1) registers[i] <= 0;
        end else if (ena) begin
            if (!run) begin
                pc <= 0;
                wait_count <= 0;
                pins_out <= 0;
                direction <= 0;
                halted <= 0;
                fault <= 0;
                for (i = 0; i < 4; i = i + 1) registers[i] <= 0;
                if (control[6]) begin
                    if (control[5]) begin
                        program_mem[control[4:0]][15:8] <= pins_in;
                        valid_high[control[4:0]] <= 1'b1;
                    end else begin
                        program_mem[control[4:0]][7:0] <= pins_in;
                        valid_low[control[4:0]] <= 1'b1;
                    end
                end
            end else if (!halted) begin
                if (wait_count != 0) begin
                    wait_count <= wait_count - 1'b1;
                end else if (!ready || opcode == 4'he) begin
                    halted <= 1'b1;
                    fault <= 1'b1;
                end else begin
                    pc <= pc + 1'b1;
                    case (opcode)
                        4'h0: pins_out <= immediate;
                        4'h1: wait_count <= immediate;
                        4'h2: pc <= immediate[4:0];
                        4'h3: registers[rd] <= pins_in;
                        4'h4: registers[rd] <= immediate;
                        4'h5: registers[rd] <= registers[rs];
                        4'h6: if (registers[rd] != 0) pc <= immediate[4:0];
                        4'h7: registers[rd] <= {1'b0, registers[rd][7:1]};
                        4'h8: registers[rd] <= {registers[rd][6:0], 1'b0};
                        4'h9: pins_out <= registers[rd];
                        4'ha: direction <= immediate;
                        4'hb: registers[rd] <= registers[rd] & immediate;
                        4'hc: registers[rd] <= registers[rd] + immediate;
                        4'hd: begin halted <= 1'b1; pc <= pc; end
                        default: begin end // Opcode F: NOP.
                    endcase
                end
            end
        end
    end
endmodule
`default_nettype wire
