import type { FormEvent } from "react";
import { Plus } from "lucide-react";
import type { FinanceCategory } from "@/lib/types";
import { DeletableList } from "../DeletableList";
import { Field } from "../Field";
import { Panel } from "../Panel";

export function CategoriesTab({
  categories,
  categoryForm,
  setCategoryForm,
  onCreate,
  onDelete,
  busy,
}: {
  categories: FinanceCategory[];
  categoryForm: { name: string; notes: string };
  setCategoryForm: React.Dispatch<React.SetStateAction<{ name: string; notes: string }>>;
  onCreate: (e: FormEvent) => void;
  onDelete: (id: string) => void;
  busy: boolean;
}) {
  return (
    <section className="grid gap-4 xl:grid-cols-[0.9fr,1.1fr]">
      <form
        onSubmit={onCreate}
        className="card-outline space-y-3"
      >
        <h2 className="kicker flex items-center gap-1.5 text-[11px]">
          <Plus className="h-3 w-3" /> New category
        </h2>
        <Field label="Name">
          <input
            required
            value={categoryForm.name}
            onChange={(e) => setCategoryForm((p) => ({ ...p, name: e.target.value }))}
            className="input"
            placeholder="Groceries"
          />
        </Field>
        <Field label="Notes (optional)">
          <input
            value={categoryForm.notes}
            onChange={(e) => setCategoryForm((p) => ({ ...p, notes: e.target.value }))}
            className="input"
          />
        </Field>
        <button
          type="submit"
          disabled={busy}
          className="btn btn-primary"
        >
          {busy ? "Creating…" : "Create category"}
        </button>
      </form>
      <Panel title="Categories">
        <DeletableList
          rows={categories.map((c) => ({
            id: c.id,
            title: c.name,
            subtitle: c.notes || undefined,
          }))}
          empty="No categories yet."
          busy={busy}
          onDelete={onDelete}
        />
      </Panel>
    </section>
  );
}
