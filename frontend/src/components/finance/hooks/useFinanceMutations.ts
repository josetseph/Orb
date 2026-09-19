import { useLayoutEffect, useState, type FormEvent, type MutableRefObject } from "react";
import { api } from "@/lib/api";
import { errMessage, monthStartIso, todayIso, tomorrowIso } from "../utils";
import type { FinanceWorkspaceState, FormSeeders } from "./useFinanceWorkspace";

export function useFinanceMutations(
  ws: FinanceWorkspaceState,
  currentKB: string,
  formSeedersRef: MutableRefObject<FormSeeders | null>,
) {
  const {
    workspace,
    currency,
    setBusy,
    setError,
    refresh,
    setSearchResult,
    setReport,
  } = ws;

  const [accountForm, setAccountForm] = useState({
    name: "",
    account_type: "asset",
    opening_balance: "",
  });
  const [txForm, setTxForm] = useState({
    type: "withdrawal",
    description: "",
    amount: "",
    account_id: "",
    transfer_account_id: "",
    counterparty_name: "",
    category: "",
    budget_id: "",
    date: todayIso(),
  });
  const [budgetForm, setBudgetForm] = useState({ name: "", amount: "" });
  const [categoryForm, setCategoryForm] = useState({ name: "", notes: "" });
  const [recurrenceForm, setRecurrenceForm] = useState({
    title: "",
    amount: "",
    type: "withdrawal",
    source_id: "",
    destination_id: "",
    description: "",
    first_date: tomorrowIso(),
    repeat_freq: "monthly",
  });
  const [ruleGroupForm, setRuleGroupForm] = useState({ title: "", description: "" });
  const [ruleForm, setRuleForm] = useState({
    title: "",
    rule_group_id: "",
    trigger_type: "description_contains",
    trigger_value: "",
    action_type: "set_category",
    action_value: "",
  });
  const [searchForm, setSearchForm] = useState({
    query: "",
    kind: "transactions" as "transactions" | "accounts",
  });
  const [reportStart, setReportStart] = useState(monthStartIso());
  const [reportEnd, setReportEnd] = useState(todayIso());

  // Keep ref always current so workspace refresh can seed default account/rule-group ids.
  useLayoutEffect(() => {
    formSeedersRef.current = { setTxForm, setRecurrenceForm, setRuleForm };
  }, [formSeedersRef, setTxForm, setRecurrenceForm, setRuleForm]);

  async function setPrimaryCurrency(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createFinanceWorkspace(currency, currentKB);
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not set primary currency."));
    } finally {
      setBusy(false);
    }
  }

  async function createAccount(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createFinanceAccount(
        {
          name: accountForm.name,
          account_type: accountForm.account_type,
          opening_balance: Number(accountForm.opening_balance || 0),
          currency: workspace?.currency || currency,
        },
        currentKB,
      );
      setAccountForm({ name: "", account_type: "asset", opening_balance: "" });
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not create account."));
    } finally {
      setBusy(false);
    }
  }

  async function createTransaction(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createFinanceTransaction(
        {
          type: txForm.type,
          description: txForm.description,
          amount: Number(txForm.amount),
          account_id: txForm.account_id,
          transfer_account_id: txForm.transfer_account_id || undefined,
          counterparty_name: txForm.counterparty_name || undefined,
          category: txForm.category || undefined,
          budget_id: txForm.budget_id || undefined,
          currency: workspace?.currency || currency,
          date: txForm.date,
        },
        currentKB,
      );
      setTxForm((prev) => ({
        ...prev,
        description: "",
        amount: "",
        counterparty_name: "",
        category: "",
        budget_id: "",
        transfer_account_id: "",
      }));
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not create transaction."));
    } finally {
      setBusy(false);
    }
  }

  async function removeTransaction(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteFinanceTransaction(id, currentKB);
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not delete transaction."));
    } finally {
      setBusy(false);
    }
  }

  async function createBudget(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createFinanceBudget(
        {
          name: budgetForm.name,
          amount: budgetForm.amount ? Number(budgetForm.amount) : undefined,
          currency: workspace?.currency || currency,
        },
        currentKB,
      );
      setBudgetForm({ name: "", amount: "" });
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not create budget."));
    } finally {
      setBusy(false);
    }
  }

  async function createCategory(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createFinanceCategory(
        { name: categoryForm.name, notes: categoryForm.notes || undefined },
        currentKB,
      );
      setCategoryForm({ name: "", notes: "" });
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not create category."));
    } finally {
      setBusy(false);
    }
  }

  async function removeCategory(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteFinanceCategory(id, currentKB);
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not delete category."));
    } finally {
      setBusy(false);
    }
  }

  async function createRecurrence(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createFinanceRecurrence(
        {
          title: recurrenceForm.title,
          amount: Number(recurrenceForm.amount),
          type: recurrenceForm.type,
          source_id: recurrenceForm.source_id,
          destination_id: recurrenceForm.destination_id,
          description: recurrenceForm.description || undefined,
          first_date: recurrenceForm.first_date,
          repeat_freq: recurrenceForm.repeat_freq,
        },
        currentKB,
      );
      setRecurrenceForm((prev) => ({
        ...prev,
        title: "",
        amount: "",
        description: "",
        destination_id: "",
        first_date: tomorrowIso(),
      }));
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not create recurring transaction."));
    } finally {
      setBusy(false);
    }
  }

  async function removeRecurrence(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteFinanceRecurrence(id, currentKB);
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not delete recurring transaction."));
    } finally {
      setBusy(false);
    }
  }

  async function createRuleGroup(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createFinanceRuleGroup(
        { title: ruleGroupForm.title, description: ruleGroupForm.description || undefined },
        currentKB,
      );
      setRuleGroupForm({ title: "", description: "" });
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not create rule group."));
    } finally {
      setBusy(false);
    }
  }

  async function removeRuleGroup(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteFinanceRuleGroup(id, currentKB);
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not delete rule group."));
    } finally {
      setBusy(false);
    }
  }

  async function createRule(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createFinanceRule(ruleForm, currentKB);
      setRuleForm((prev) => ({
        ...prev,
        title: "",
        trigger_value: "",
        action_value: "",
      }));
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not create rule."));
    } finally {
      setBusy(false);
    }
  }

  async function removeRule(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteFinanceRule(id, currentKB);
      await refresh();
    } catch (err) {
      setError(errMessage(err, "Could not delete rule."));
    } finally {
      setBusy(false);
    }
  }

  async function runSearch(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.searchFinance(searchForm.query, searchForm.kind, currentKB);
      setSearchResult(result);
    } catch (err) {
      setError(errMessage(err, "Search failed."));
    } finally {
      setBusy(false);
    }
  }

  async function loadReport(e?: FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload = await api.getFinanceReport(currentKB, reportStart, reportEnd);
      setReport(payload);
    } catch (err) {
      setError(errMessage(err, "Could not load report."));
    } finally {
      setBusy(false);
    }
  }

  async function resetAdministration() {
    if (
      !window.confirm(
        `Clear all finance data for knowledge base “${currentKB}”?\n\n` +
          `This destroys the Firefly administration (accounts, transactions, budgets, …). ` +
          `Opening Finance again creates a fresh empty administration.`,
      )
    ) {
      return;
    }
    if (
      !window.confirm(
        "Confirm again: permanently delete this KB’s finance administration?",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.resetFinanceAdministration(currentKB);
      await refresh();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to clear finance data",
      );
    } finally {
      setBusy(false);
    }
  }

  return {
    accountForm,
    setAccountForm,
    txForm,
    setTxForm,
    budgetForm,
    setBudgetForm,
    categoryForm,
    setCategoryForm,
    recurrenceForm,
    setRecurrenceForm,
    ruleGroupForm,
    setRuleGroupForm,
    ruleForm,
    setRuleForm,
    searchForm,
    setSearchForm,
    reportStart,
    setReportStart,
    reportEnd,
    setReportEnd,
    setPrimaryCurrency,
    createAccount,
    createTransaction,
    removeTransaction,
    createBudget,
    createCategory,
    removeCategory,
    createRecurrence,
    removeRecurrence,
    createRuleGroup,
    removeRuleGroup,
    createRule,
    removeRule,
    runSearch,
    loadReport,
    resetAdministration,
  };
}

export type FinanceMutationsState = ReturnType<typeof useFinanceMutations>;
