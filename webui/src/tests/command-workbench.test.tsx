import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CommandWorkbenchPanel } from "@/components/command-workbench/CommandWorkbench";
import type { CommandOpsSnapshot, CommandWorkbenchNotice } from "@/lib/command-ops";
import { approvalShaFor } from "@/lib/command-ops";

const SNAPSHOT: CommandOpsSnapshot = {
  catalog: { status: "unavailable", detail: "taptap --describe-json is not available on this machine" },
  pending: [{
    template: "quantitative",
    step_id: "fit_baseline",
    name: "基线拟合",
    summary: "基线漂多少",
    describe_json_todo: "TODO: taptap --describe-json",
    catalog_source: null,
  }],
  runs: [{
    run_id: "run-1",
    template: "quantitative",
    status: "active",
    accepted: false,
    progress: { done: 1, total: 7, current_step: "pulse_size" },
    steps: [
      { step_id: "init_tap", name: "项目就绪", summary: "有没有配置", status: "done", op_id: "op-0" },
      { step_id: "pulse_size", name: "脉冲量", summary: "每脉冲多少 mol", status: "current", op_id: "op-1" },
    ],
  }],
  ops: [{
    op_id: "op-1",
    contract_ref: "process_pulse_sizes/pulse_size",
    status: "awaiting_approval",
    blocked_reason: "need_approval",
    params: { pressure_unit: "bar" },
    argv_sha256: "sha-from-server",
    validation_errors: [],
    parent_run_id: "run-1",
    step_id: "pulse_size",
    actions: { fill: true, approve: true, cancel: true },
    fields: [{
      name: "pressure_unit",
      type: "string",
      required: true,
      enum: ["bar", "mbar"],
      flag: "--pressure-unit",
      positional: null,
    }],
    run: null,
    artifacts: [],
  }],
};

describe("command workbench", () => {
  it("binds approve to the sha from op.need_approval", () => {
    const onApprove = vi.fn();
    const notice: CommandWorkbenchNotice = {
      event: "command_op",
      name: "op.need_approval",
      op_id: "op-1",
      status: "awaiting_approval",
      argv_sha256: "sha-from-event",
    };
    expect(approvalShaFor(SNAPSHOT.ops[0]!, notice)).toBe("sha-from-event");
    const { rerender } = render(
      <CommandWorkbenchPanel
        snapshot={SNAPSHOT}
        notices={{ "op-1": notice }}
        onFill={vi.fn()}
        onApprove={onApprove}
        onCancel={vi.fn()}
        onStartRun={vi.fn()}
      />,
    );
    expect(screen.getByText("1 of 7")).toBeInTheDocument();
    expect(screen.getByText("Current step: 脉冲量")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith("op-1", "sha-from-event");

    rerender(
      <CommandWorkbenchPanel
        snapshot={SNAPSHOT}
        notices={{
          "op-1": { ...notice, name: "op.need_input", argv_sha256: null, status: "need_input" },
        }}
        onFill={vi.fn()}
        onApprove={onApprove}
        onCancel={vi.fn()}
        onStartRun={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("saves form values and cancels from the same op", () => {
    const onFill = vi.fn();
    const onApprove = vi.fn();
    const onCancel = vi.fn();
    render(
      <CommandWorkbenchPanel
        snapshot={SNAPSHOT}
        notices={{}}
        onFill={onFill}
        onApprove={onApprove}
        onCancel={onCancel}
        onStartRun={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/pressure_unit/), { target: { value: "mbar" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onFill).toHaveBeenCalledWith("op-1", { pressure_unit: "mbar" });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith("op-1", "sha-from-server");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledWith("op-1");
  });
});
