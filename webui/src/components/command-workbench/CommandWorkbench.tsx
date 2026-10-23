import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { fetchCommandOps, mutateCommandOp } from "@/lib/api";
import {
  COMMAND_OPS_CAPABILITY,
  approvalShaFor,
  type CommandOpView,
  type CommandOpsSnapshot,
  type CommandWorkbenchNotice,
} from "@/lib/command-ops";
import { useClient } from "@/providers/ClientProvider";

export function CommandWorkbenchPanel({
  snapshot,
  notices,
  onFill,
  onApprove,
  onCancel,
  onStartRun,
}: {
  snapshot: CommandOpsSnapshot;
  notices: Record<string, CommandWorkbenchNotice>;
  onFill: (opId: string, params: Record<string, string | number | boolean>) => void;
  onApprove: (opId: string, sha: string) => void;
  onCancel: (opId: string) => void;
  onStartRun: (template: "quantitative" | "kinetics") => void;
}) {
  const { t } = useTranslation();
  const catalogKey = snapshot.catalog.status === "live"
    ? "commandWorkbench.live"
    : snapshot.catalog.status === "invalid"
      ? "commandWorkbench.invalid"
      : snapshot.catalog.status === "unavailable"
        ? "commandWorkbench.unavailable"
        : "commandWorkbench.unchecked";
  const waiting = snapshot.pending.filter((item) => !item.catalog_source);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">{t(catalogKey)}</p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={() => onStartRun("quantitative")}>
          {t("commandWorkbench.startQuantitative")}
        </Button>
        <Button type="button" variant="outline" onClick={() => onStartRun("kinetics")}>
          {t("commandWorkbench.startKinetics")}
        </Button>
      </div>
      {snapshot.runs.map((run) => {
        const total = run.progress.total;
        const done = run.progress.done;
        const current = run.steps.find((step) => step.step_id === run.progress.current_step);
        return (
          <section key={run.run_id} className="flex flex-col gap-2" aria-label={run.template}>
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="text-sm font-medium">{run.template}</h3>
              <p>{t("commandWorkbench.progress", { done, total })}</p>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
              <div
                className="h-2 bg-foreground"
                style={{ width: `${total === 0 ? 0 : (done / total) * 100}%` }}
              />
            </div>
            <p className="text-sm">
              {t("commandWorkbench.current")}: {current?.name ?? run.progress.current_step ?? "—"}
            </p>
            <ol className="flex flex-col gap-1 text-sm">
              {run.steps.map((step) => (
                <li key={step.step_id}>
                  {step.name} · {step.status}
                </li>
              ))}
            </ol>
            {run.status === "ready_for_acceptance" ? (
              <p className="text-sm text-muted-foreground">{t("commandWorkbench.acceptance")}</p>
            ) : null}
          </section>
        );
      })}
      {snapshot.ops.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("commandWorkbench.noOps")}</p>
      ) : null}
      {snapshot.ops.map((op) => (
        <OpCard
          key={op.op_id}
          op={op}
          notice={notices[op.op_id] ?? null}
          onFill={onFill}
          onApprove={onApprove}
          onCancel={onCancel}
        />
      ))}
      {waiting.length > 0 ? (
        <section className="flex flex-col gap-1">
          <h3 className="text-sm font-medium">{t("commandWorkbench.pending")}</h3>
          <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
            {waiting.map((item) => (
              <li key={`${item.template}:${item.step_id}`}>
                {item.name} · {item.summary}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function OpCard({
  op,
  notice,
  onFill,
  onApprove,
  onCancel,
}: {
  op: CommandOpView;
  notice: CommandWorkbenchNotice | null;
  onFill: (opId: string, params: Record<string, string | number | boolean>) => void;
  onApprove: (opId: string, sha: string) => void;
  onCancel: (opId: string) => void;
}) {
  const { t } = useTranslation();
  const sha = approvalShaFor(op, notice);
  const [draft, setDraft] = useState(() => draftFrom(op));
  const draftKey = `${op.op_id}:${op.status}:${JSON.stringify(op.params)}`;
  useEffect(() => {
    setDraft(draftFrom(op));
  }, [draftKey, op]);
  const statusLabel = op.blocked_reason === "need_input"
    ? t("commandWorkbench.needsInput")
    : op.blocked_reason === "need_approval"
      ? t("commandWorkbench.needsApproval")
      : op.status;
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-input p-3" aria-label={op.contract_ref}>
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium">{op.contract_ref}</h3>
        <p className="text-sm text-muted-foreground">{statusLabel}</p>
      </div>
      {op.validation_errors.length > 0 ? (
        <ul className="text-sm text-destructive">
          {op.validation_errors.map((error) => <li key={error}>{error}</li>)}
        </ul>
      ) : null}
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          onFill(op.op_id, paramsFrom(op, draft));
        }}
      >
        {op.fields.map((field) => {
          const id = `${op.op_id}-${field.name}`;
          const label = field.flag ? `${field.name} (${field.flag})` : field.name;
          return (
            <label key={field.name} className="flex flex-col gap-1 text-sm" htmlFor={id}>
              <span>
                {label}
                {field.required ? ` · ${t("commandWorkbench.required")}` : ""}
              </span>
              {field.enum ? (
                <select
                  id={id}
                  className="h-10 rounded-full border border-input bg-background px-3 text-sm"
                  value={draft[field.name] ?? ""}
                  disabled={!op.actions.fill}
                  onChange={(event) => setDraft({ ...draft, [field.name]: event.target.value })}
                >
                  <option value="">{t("commandWorkbench.required")}</option>
                  {field.enum.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
                </select>
              ) : field.type === "boolean" ? (
                <input
                  id={id}
                  type="checkbox"
                  checked={draft[field.name] === "true"}
                  disabled={!op.actions.fill}
                  onChange={(event) => setDraft({
                    ...draft,
                    [field.name]: event.target.checked ? "true" : "false",
                  })}
                />
              ) : (
                <Input
                  id={id}
                  type={field.type === "integer" || field.type === "number" ? "number" : "text"}
                  value={draft[field.name] ?? ""}
                  disabled={!op.actions.fill}
                  onChange={(event) => setDraft({ ...draft, [field.name]: event.target.value })}
                />
              )}
            </label>
          );
        })}
        <div className="flex flex-wrap gap-2">
          {op.actions.fill ? (
            <Button type="submit">{t("commandWorkbench.fill")}</Button>
          ) : null}
          <Button
            type="button"
            disabled={sha === null || !op.actions.approve}
            onClick={() => {
              if (sha) onApprove(op.op_id, sha);
            }}
          >
            {t("commandWorkbench.approve")}
          </Button>
          {op.actions.cancel ? (
            <Button type="button" variant="outline" onClick={() => onCancel(op.op_id)}>
              {t("commandWorkbench.cancel")}
            </Button>
          ) : null}
        </div>
      </form>
      {op.run ? (
        <p className="text-sm">
          {t("commandWorkbench.exitCode")}: {op.run.exit_code ?? "—"}
          {op.run.log_uri ? ` · ${t("commandWorkbench.log")} ${op.run.log_uri}` : ""}
        </p>
      ) : null}
      <p className="text-sm text-muted-foreground">{t("commandWorkbench.approveHint")}</p>
    </section>
  );
}

function draftFrom(op: CommandOpView): Record<string, string> {
  const draft: Record<string, string> = {};
  for (const field of op.fields) {
    const value = op.params[field.name];
    if (typeof value === "boolean") draft[field.name] = value ? "true" : "false";
    else if (value === undefined) draft[field.name] = "";
    else draft[field.name] = String(value);
  }
  return draft;
}

function paramsFrom(
  op: CommandOpView,
  draft: Record<string, string>,
): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {};
  for (const field of op.fields) {
    const raw = draft[field.name] ?? "";
    if (field.type === "boolean") {
      params[field.name] = raw === "true";
      continue;
    }
    if (raw.trim() === "") continue;
    if (field.type === "integer" || field.type === "number") {
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) {
        params[field.name] = field.type === "integer" ? Math.trunc(parsed) : parsed;
      }
      continue;
    }
    params[field.name] = raw;
  }
  return params;
}

export function CommandWorkbenchHost() {
  const { t } = useTranslation();
  const { client, getToken, webuiCapabilities } = useClient();
  const enabled = webuiCapabilities.includes(COMMAND_OPS_CAPABILITY);
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<CommandOpsSnapshot | null>(null);
  const [notices, setNotices] = useState<Record<string, CommandWorkbenchNotice>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return undefined;
    return client.onCommandWorkbench((notice) => {
      if (notice.event === "command_op") {
        setNotices((current) => ({ ...current, [notice.op_id]: notice }));
      }
      void fetchCommandOps(getToken()).then(setSnapshot).catch(() => {
        setError(t("commandWorkbench.error"));
      });
    });
  }, [client, enabled, getToken, t]);

  if (!enabled) return null;

  const refresh = () => {
    void fetchCommandOps(getToken()).then((next) => {
      setSnapshot(next);
      setError(null);
    }).catch(() => setError(t("commandWorkbench.error")));
  };

  return (
    <>
      <Button
        type="button"
        className="fixed bottom-4 right-4 z-40"
        onClick={() => {
          setOpen(true);
          refresh();
        }}
      >
        {t("commandWorkbench.open")}
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-[min(100vw,28rem)] overflow-y-auto" aria-describedby={undefined}>
          <SheetTitle>{t("commandWorkbench.title")}</SheetTitle>
          <SheetDescription className="sr-only">{t("commandWorkbench.approveHint")}</SheetDescription>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          {snapshot ? (
            <CommandWorkbenchPanel
              snapshot={snapshot}
              notices={notices}
              onFill={(opId, params) => {
                void mutateCommandOp(client, "fill", { op_id: opId, params }).then((next) => {
                  setSnapshot(next);
                  setError(null);
                }).catch(() => setError(t("commandWorkbench.error")));
              }}
              onApprove={(opId, sha) => {
                void mutateCommandOp(client, "approve", { op_id: opId, sha }).then((next) => {
                  setSnapshot(next);
                  setError(null);
                }).catch(() => setError(t("commandWorkbench.error")));
              }}
              onCancel={(opId) => {
                void mutateCommandOp(client, "cancel", { op_id: opId }).then((next) => {
                  setSnapshot(next);
                  setError(null);
                }).catch(() => setError(t("commandWorkbench.error")));
              }}
              onStartRun={(template) => {
                void mutateCommandOp(client, "start_run", { template }).then((next) => {
                  setSnapshot(next);
                  setError(null);
                }).catch(() => setError(t("commandWorkbench.error")));
              }}
            />
          ) : (
            <p className="text-sm text-muted-foreground">{t("commandWorkbench.loading")}</p>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
