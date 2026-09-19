import type { FormEvent } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { FinanceRule, FinanceRuleGroup } from "@/lib/types";
import { DeletableList } from "../DeletableList";
import { Field } from "../Field";
import { Panel } from "../Panel";
import { DELETE_BTN, EMPTY_ROW, LIST_ROW } from "../utils";

type RuleGroupForm = { title: string; description: string };
type RuleForm = {
  title: string;
  rule_group_id: string;
  trigger_type: string;
  trigger_value: string;
  action_type: string;
  action_value: string;
};

export function RulesTab({
  ruleGroups,
  rules,
  ruleGroupForm,
  setRuleGroupForm,
  ruleForm,
  setRuleForm,
  onCreateGroup,
  onCreateRule,
  onDeleteGroup,
  onDeleteRule,
  busy,
}: {
  ruleGroups: FinanceRuleGroup[];
  rules: FinanceRule[];
  ruleGroupForm: RuleGroupForm;
  setRuleGroupForm: React.Dispatch<React.SetStateAction<RuleGroupForm>>;
  ruleForm: RuleForm;
  setRuleForm: React.Dispatch<React.SetStateAction<RuleForm>>;
  onCreateGroup: (e: FormEvent) => void;
  onCreateRule: (e: FormEvent) => void;
  onDeleteGroup: (id: string) => void;
  onDeleteRule: (id: string) => void;
  busy: boolean;
}) {
  return (
    <section className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <form
          onSubmit={onCreateGroup}
          className="card-outline space-y-3"
        >
          <h2 className="kicker flex items-center gap-1.5 text-[11px]">
            <Plus className="h-3 w-3" /> Rule group
          </h2>
          <Field label="Title">
            <input
              required
              value={ruleGroupForm.title}
              onChange={(e) => setRuleGroupForm((p) => ({ ...p, title: e.target.value }))}
              className="input"
            />
          </Field>
          <Field label="Description">
            <input
              value={ruleGroupForm.description}
              onChange={(e) =>
                setRuleGroupForm((p) => ({ ...p, description: e.target.value }))
              }
              className="input"
            />
          </Field>
          <button
            type="submit"
            disabled={busy}
            className="btn btn-primary"
          >
            Create group
          </button>
        </form>
        <form
          onSubmit={onCreateRule}
          className="card-outline space-y-3"
        >
          <h2 className="kicker flex items-center gap-1.5 text-[11px]">
            <Plus className="h-3 w-3" /> Rule
          </h2>
          <Field label="Title">
            <input
              required
              value={ruleForm.title}
              onChange={(e) => setRuleForm((p) => ({ ...p, title: e.target.value }))}
              className="input"
            />
          </Field>
          <Field label="Rule group">
            <select
              required
              value={ruleForm.rule_group_id}
              onChange={(e) =>
                setRuleForm((p) => ({ ...p, rule_group_id: e.target.value }))
              }
              className="input"
            >
              <option value="">Select group</option>
              {ruleGroups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.title}
                </option>
              ))}
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="If description contains">
              <input
                required
                value={ruleForm.trigger_value}
                onChange={(e) =>
                  setRuleForm((p) => ({ ...p, trigger_value: e.target.value }))
                }
                className="input"
                placeholder="UBER"
              />
            </Field>
            <Field label="Then set category">
              <input
                required
                value={ruleForm.action_value}
                onChange={(e) =>
                  setRuleForm((p) => ({ ...p, action_value: e.target.value }))
                }
                className="input"
                placeholder="Transport"
              />
            </Field>
          </div>
          <button
            type="submit"
            disabled={busy || !ruleForm.rule_group_id}
            className="btn btn-primary"
          >
            Create rule
          </button>
        </form>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Rule groups">
          <DeletableList
            rows={ruleGroups.map((g) => ({
              id: g.id,
              title: g.title,
              subtitle: g.description || undefined,
            }))}
            empty="No rule groups yet."
            busy={busy}
            onDelete={onDeleteGroup}
          />
        </Panel>
        <Panel title="Rules">
          <ul>
            {rules.map((rule) => (
              <li
                key={rule.id}
                className={LIST_ROW}
              >
                <div className="min-w-0">
                  <div className="truncate">{rule.title}</div>
                  <div className="text-[11px] text-n-500">
                    {rule.triggers[0]?.type || "trigger"}:{rule.triggers[0]?.value || "—"}{" "}
                    → {rule.actions[0]?.type || "action"}:{rule.actions[0]?.value || "—"}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onDeleteRule(rule.id)}
                  className={DELETE_BTN}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
            {rules.length === 0 && (
              <li className={EMPTY_ROW}>
                No rules yet.
              </li>
            )}
          </ul>
        </Panel>
      </div>
    </section>
  );
}
