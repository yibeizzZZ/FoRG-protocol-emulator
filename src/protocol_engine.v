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
    reg [15:0] program_mem [0:127];
    reg [127:0] valid_low, valid_high;
    reg [7:0] data_mem [0:31];
    reg [31:0] data_valid;
    // Keep operand reads parallel; shared memory ports serialize fault checking.
    (* mem2reg *) reg [7:0] registers [0:3];
    reg [6:0] pc;
    reg [11:0] wait_count;
    reg [7:0] direction;
    reg halted, fault, extended;
    reg [2:0] host_bank;
    // UI-only host packets leave the shared protocol pins entirely untouched.
    reg [19:0] packet_shift;
    reg [2:0] packet_phase;
    reg packet_strobe;
    wire [7:0] packet_address = packet_shift[19:12];
    wire [15:0] packet_value = {packet_shift[11:0], control[3:0]};
    reg [6:0] return_stack [0:3];
    reg [2:0] stack_depth;
    wire run = control[7];
    wire [15:0] instruction = program_mem[pc];
    wire [3:0] opcode = instruction[15:12];
    wire [3:0] subop = instruction[11:8];
    wire [1:0] rd = instruction[11:10];
    wire [1:0] rs = instruction[9:8];
    wire [7:0] immediate = instruction[7:0];
    wire [6:0] next_pc = extended ? pc + 7'd1 : {2'b00, pc[4:0] + 5'd1};
    wire [6:0] target = extended ? immediate[6:0] : {2'b00, immediate[4:0]};
    wire [6:0] host_address = {host_bank[1:0], control[4:0]};
    wire [7:0] indirect_address = registers[immediate[1:0]];
    wire ready = valid_low[pc] && valid_high[pc];
    wire invalid_data_read = opcode == 4'he &&
        ((subop == 4'h6 && !data_valid[immediate[4:0]]) ||
         (subop == 4'h8 && !data_valid[indirect_address[4:0]]));
    wire invalid_indirect = opcode == 4'he && (subop == 4'h8 || subop == 4'h9) &&
        (indirect_address[7:5] != 0);
    wire invalid_stack = opcode == 4'he &&
        ((subop == 4'ha && stack_depth == 4) || (subop == 4'hb && stack_depth == 0));
    wire invalid_opcode = extended ?
        ((opcode == 4'he && subop >= 4'he) || (opcode == 4'hf && subop >= 4'hd)) :
        opcode == 4'he;
    integer i;

    assign pins_oe = (run && rst_n && !halted && !fault) ? direction : 8'b0;
    assign readback = control[6:5] == 2'b00 ?
                          (extended ? (data_valid[control[4:0]] ? data_mem[control[4:0]] : 8'b0) : pins_out) :
                      control[6:5] == 2'b01 ? {fault, halted, (wait_count != 0), pc[4:0]} :
                      control[6:5] == 2'b10 ? registers[control[1:0]] :
                      extended && control[4:0] == 1 ? {1'b0, pc} :
                      extended && control[4:0] == 2 ? pins_out : direction;

    always @(posedge clk) begin
        if (!rst_n) begin
            valid_low <= 0;
            valid_high <= 0;
            data_valid <= 0;
            extended <= 0;
            host_bank <= 0;
            packet_phase <= 0;
            packet_strobe <= 0;
            stack_depth <= 0;
            pc <= 0;
            wait_count <= 0;
            pins_out <= 0;
            direction <= 0;
            halted <= 0;
            fault <= 0;
            for (i = 0; i < 4; i = i + 1) registers[i] <= 0;
        end else if (ena) begin
            if (run) begin
                packet_phase <= 0;
                packet_strobe <= 0;
            end
            if (!run) begin
                pc <= 0;
                wait_count <= 0;
                pins_out <= 0;
                direction <= 0;
                halted <= 0;
                fault <= 0;
                stack_depth <= 0;
                for (i = 0; i < 4; i = i + 1) registers[i] <= 0;
                if (control == 8'h1e) begin
                    packet_phase <= 0;
                    packet_strobe <= 0;
                end else if (control[6:5] == 2'b01) begin
                    if (control[4] != packet_strobe) begin
                        packet_strobe <= control[4];
                        if (packet_phase == 5) begin
                            packet_phase <= 0;
                            if (!packet_address[7]) begin
                                program_mem[packet_address[6:0]] <= packet_value;
                                valid_low[packet_address[6:0]] <= 1'b1;
                                valid_high[packet_address[6:0]] <= 1'b1;
                            end else if (packet_address[7:5] == 3'b100 && packet_value[15:8] == 0) begin
                                data_mem[packet_address[4:0]] <= packet_value[7:0];
                                data_valid[packet_address[4:0]] <= 1'b1;
                            end else if (packet_address == 8'hff && packet_value[15:1] == 0) begin
                                extended <= packet_value[0];
                                host_bank <= 0;
                            end
                        end else begin
                            packet_shift <= {packet_shift[15:0], control[3:0]};
                            packet_phase <= packet_phase + 1'b1;
                        end
                    end
                end else if (control == 8'h1f) begin
                    extended <= pins_in[7];
                    host_bank <= pins_in[2:0];
                end else if (control[6]) begin
                    if (host_bank < 4) begin
                        if (control[5]) begin
                            program_mem[host_address][15:8] <= pins_in;
                            valid_high[host_address] <= 1'b1;
                        end else begin
                            program_mem[host_address][7:0] <= pins_in;
                            valid_low[host_address] <= 1'b1;
                        end
                    end else if (host_bank == 4 && !control[5]) begin
                        data_mem[control[4:0]] <= pins_in;
                        data_valid[control[4:0]] <= 1'b1;
                    end
                end
            end else if (!halted) begin
                if (wait_count != 0) begin
                    wait_count <= wait_count - 1'b1;
                end else if (!ready || invalid_opcode ||
                             (extended && (invalid_data_read || invalid_indirect || invalid_stack))) begin
                    halted <= 1'b1;
                    fault <= 1'b1;
                end else begin
                    pc <= next_pc;
                    case (opcode)
                        4'h0: pins_out <= immediate;
                        4'h1: wait_count <= extended ? instruction[11:0] : {4'b0, immediate};
                        4'h2: pc <= target;
                        4'h3: registers[rd] <= pins_in;
                        4'h4: registers[rd] <= immediate;
                        4'h5: registers[rd] <= registers[rs];
                        4'h6: if (registers[rd] != 0) pc <= target;
                        4'h7: registers[rd] <= {1'b0, registers[rd][7:1]};
                        4'h8: registers[rd] <= {registers[rd][6:0], 1'b0};
                        4'h9: pins_out <= registers[rd];
                        4'ha: direction <= immediate;
                        4'hb: registers[rd] <= registers[rd] & immediate;
                        4'hc: registers[rd] <= registers[rd] + immediate;
                        4'hd: begin halted <= 1'b1; pc <= pc; end
                        4'he: begin
                            case (subop)
                                4'h0, 4'h1, 4'h2, 4'h3:
                                    if (registers[subop[1:0]] == 0) pc <= target;
                                4'h4: direction <= direction | immediate;
                                4'h5: direction <= direction & ~immediate;
                                4'h6: registers[immediate[7:6]] <= data_mem[immediate[4:0]];
                                4'h7: begin
                                    data_mem[immediate[4:0]] <= registers[immediate[7:6]];
                                    data_valid[immediate[4:0]] <= 1'b1;
                                end
                                4'h8: registers[immediate[3:2]] <= data_mem[indirect_address[4:0]];
                                4'h9: begin
                                    data_mem[indirect_address[4:0]] <= registers[immediate[3:2]];
                                    data_valid[indirect_address[4:0]] <= 1'b1;
                                end
                                4'ha: begin
                                    return_stack[stack_depth[1:0]] <= next_pc;
                                    stack_depth <= stack_depth + 1'b1;
                                    pc <= target;
                                end
                                4'hb: begin
                                    pc <= return_stack[stack_depth[1:0] - 2'd1];
                                    stack_depth <= stack_depth - 1'b1;
                                end
                                4'hc: registers[immediate[3:2]] <=
                                          registers[immediate[3:2]] | registers[immediate[1:0]];
                                4'hd: direction <= registers[immediate[1:0]];
                                default: begin end // Invalid encodings fault before dispatch.
                            endcase
                        end
                        4'hf: if (extended) begin
                            case (subop)
                                4'h8, 4'h9, 4'ha, 4'hb: begin
                                    registers[subop[1:0]] <= registers[subop[1:0]] - 1'b1;
                                    if (registers[subop[1:0]] != 1) pc <= target;
                                end
                                4'hc: registers[immediate[4:3]] <=
                                          {registers[immediate[4:3]][6:0], pins_in[immediate[2:0]]};
                                default: begin end // F0..F7 remain NOP; FD..FF fault above.
                            endcase
                        end
                        default: begin end
                    endcase
                end
            end
        end
    end
endmodule
`default_nettype wire
