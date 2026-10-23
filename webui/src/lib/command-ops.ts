export interface CommandField {
  name: string;
  type: "string" | "integer" | "number" | "boolean" | "path";
  required: boolean;
  enum: string[] | null;
  flag: string | null;
  positional: number | null;
}

export interface CommandOpView {
  op_id: string;
  contract_ref: string;
  status: string;
  blocked_reason: string | null;
  params: Record<string, string | number | boolean>;
  argv_sha256: string | null;
  validation_errors: string[];
  parent_run_id: string | null;
  step_id: string | null;
  actions: { fill: boolean; approve: boolean; cancel: boolean };
  fields: CommandField[];
  run: { exit_code: number | null; log_uri: string | null } | null;
  artifacts: { path: string; sha: string }[];
}

export interface TemplateRunView {
  run_id: string;
  template: string;
  status: string;
  accepted: boolean;
  progress: { done: number; total: number; current_step: string | null };
  steps: {
    step_id: string;
    name: string;
    summary: string;
    status: string;
    op_id: string | null;
  }[];
}

export interface PendingCommand {
  template: string;
  step_id: string;
  name: string;
  summary: string;
  describe_json_todo: string;
  catalog_source: string | null;
}

export interface CommandOpsSnapshot {
  catalog: { status: string; detail: string };
  ops: CommandOpView[];
  runs: TemplateRunView[];
  pending: PendingCommand[];
}

export type CommandWorkbenchNotice =
  | {
    event: "command_op";
    name: string;
    op_id: string;
    status: string;
    argv_sha256: string | null;
  }
  | {
    event: "template_run";
    run_id: string;
    done: number;
    total: number;
    current_step: string | null;
    status: string;
  };

export const COMMAND_OPS_CAPABILITY = "webui.command-ops.v1";

export function approvalShaFor(
  op: Pick<CommandOpView, "op_id" | "status" | "argv_sha256">,
  notice: CommandWorkbenchNotice | null,
): string | null {
  if (notice && notice.event === "command_op" && notice.op_id === op.op_id) {
    if (notice.name === "op.need_approval") return notice.argv_sha256;
    if (
      notice.name === "op.need_input"
      || notice.name === "op.updated"
      || notice.name === "op.cancelled"
      || notice.name === "op.approved"
    ) {
      return null;
    }
  }
  return op.status === "awaiting_approval" ? op.argv_sha256 : null;
}

export function isCommandOpsSnapshot(value: unknown): value is CommandOpsSnapshot {
  if (!value || typeof value !== "object") return false;
  const row = value as CommandOpsSnapshot;
  return Boolean(
    row.catalog
    && typeof row.catalog.status === "string"
    && Array.isArray(row.ops)
    && Array.isArray(row.runs)
    && Array.isArray(row.pending),
  );
}
