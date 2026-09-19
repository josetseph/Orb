import type { FormEvent } from "react";
import { PieChart } from "lucide-react";
import type { FinanceReport, FinanceWorkspace } from "@/lib/types";
import { AccountList } from "../AccountList";
import { BasicSummaryList } from "../BasicSummaryList";
import { ChartList } from "../ChartList";
import { Field } from "../Field";
import { Panel } from "../Panel";

export function ReportsTab({
  report,
  workspace,
  reportStart,
  reportEnd,
  setReportStart,
  setReportEnd,
  onLoad,
  busy,
}: {
  report: FinanceReport | null;
  workspace: FinanceWorkspace;
  reportStart: string;
  reportEnd: string;
  setReportStart: (value: string) => void;
  setReportEnd: (value: string) => void;
  onLoad: (e?: FormEvent) => void;
  busy: boolean;
}) {
  return (
    <section className="space-y-4">
      <form
        onSubmit={onLoad}
        className="card-outline flex flex-wrap items-end gap-3"
      >
        <Field label="Start">
          <input
            type="date"
            value={reportStart}
            onChange={(e) => setReportStart(e.target.value)}
            className="input"
          />
        </Field>
        <Field label="End">
          <input
            type="date"
            value={reportEnd}
            onChange={(e) => setReportEnd(e.target.value)}
            className="input"
          />
        </Field>
        <button
          type="submit"
          disabled={busy}
          className="btn btn-primary"
        >
          <PieChart className="h-4 w-4" />
          {busy ? "Loading…" : "Generate report"}
        </button>
      </form>
      {report && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Basic summary">
            <BasicSummaryList basic={report.basic} />
          </Panel>
          <Panel title="Category overview">
            <ChartList data={report.category_chart} />
          </Panel>
          <Panel title="Budget overview">
            <ChartList data={report.budget_chart} />
          </Panel>
          <Panel title="Accounts snapshot">
            <AccountList
              accounts={report.accounts}
              currency={workspace.currency}
              empty="No accounts."
            />
          </Panel>
        </div>
      )}
    </section>
  );
}
