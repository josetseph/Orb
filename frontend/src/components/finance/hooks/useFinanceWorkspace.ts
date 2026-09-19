import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { api } from "@/lib/api";
import type {
  FinanceAccount,
  FinanceBudget,
  FinanceCategory,
  FinanceRecurrence,
  FinanceReport,
  FinanceRule,
  FinanceRuleGroup,
  FinanceSearchResult,
  FinanceSummary,
  FinanceTransaction,
  FinanceWorkspace,
} from "@/lib/types";
import { errMessage } from "@/lib/utils";

export type FormSeeders = {
  setTxForm: React.Dispatch<
    React.SetStateAction<{
      type: string;
      description: string;
      amount: string;
      account_id: string;
      transfer_account_id: string;
      counterparty_name: string;
      category: string;
      budget_id: string;
      date: string;
    }>
  >;
  setRecurrenceForm: React.Dispatch<
    React.SetStateAction<{
      title: string;
      amount: string;
      type: string;
      source_id: string;
      destination_id: string;
      description: string;
      first_date: string;
      repeat_freq: string;
    }>
  >;
  setRuleForm: React.Dispatch<
    React.SetStateAction<{
      title: string;
      rule_group_id: string;
      trigger_type: string;
      trigger_value: string;
      action_type: string;
      action_value: string;
    }>
  >;
};

export function useFinanceWorkspace(
  currentKB: string,
  formSeedersRef: MutableRefObject<FormSeeders | null>,
) {
  const currentKBRef = useRef(currentKB);
  useEffect(() => {
    currentKBRef.current = currentKB;
  }, [currentKB]);

  const [workspace, setWorkspace] = useState<FinanceWorkspace | null>(null);
  const [summary, setSummary] = useState<FinanceSummary | null>(null);
  const [accounts, setAccounts] = useState<FinanceAccount[]>([]);
  const [transactions, setTransactions] = useState<FinanceTransaction[]>([]);
  const [budgets, setBudgets] = useState<FinanceBudget[]>([]);
  const [categories, setCategories] = useState<FinanceCategory[]>([]);
  const [recurrences, setRecurrences] = useState<FinanceRecurrence[]>([]);
  const [ruleGroups, setRuleGroups] = useState<FinanceRuleGroup[]>([]);
  const [rules, setRules] = useState<FinanceRule[]>([]);
  const [searchResult, setSearchResult] = useState<FinanceSearchResult | null>(null);
  const [report, setReport] = useState<FinanceReport | null>(null);
  const [currency, setCurrency] = useState("USD");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const assetAccounts = useMemo(
    () => accounts.filter((a) => a.account_type === "asset" || a.account_type === "Asset account"),
    [accounts],
  );
  const expenseAccounts = useMemo(
    () => accounts.filter((a) => a.account_type === "expense" || a.account_type === "Expense account"),
    [accounts],
  );
  const revenueAccounts = useMemo(
    () => accounts.filter((a) => a.account_type === "revenue" || a.account_type === "Revenue account"),
    [accounts],
  );

  const [loadedKb, setLoadedKb] = useState(currentKB);
  if (loadedKb !== currentKB) {
    setLoadedKb(currentKB);
    setLoading(true);
    setError(null);
  }

  const refresh = useCallback(async () => {
    const kb = currentKB;
    setLoading(true);
    setError(null);
    try {
      const ws = await api.getFinanceWorkspace(kb);
      if (kb !== currentKBRef.current) return;
      setWorkspace(ws);
      if (ws.currency) setCurrency(ws.currency);
      if (!ws.ready) {
        setSummary(null);
        setAccounts([]);
        setTransactions([]);
        setBudgets([]);
        setCategories([]);
        setRecurrences([]);
        setRuleGroups([]);
        setRules([]);
        setSearchResult(null);
        setReport(null);
        return;
      }

      async function loadOptional<T>(label: string, fn: () => Promise<T>, fallback: T): Promise<T> {
        try {
          return await fn();
        } catch (err) {
          console.warn(`Finance ${label} failed`, err);
          return fallback;
        }
      }

      const [
        financeSummary,
        accountRows,
        txRows,
        budgetRows,
        categoryRows,
        recurrenceRows,
        ruleGroupRows,
        ruleRows,
      ] = await Promise.all([
        api.getFinanceSummary(kb, 30),
        api.listFinanceAccounts(kb),
        api.listFinanceTransactions(kb),
        loadOptional("budgets", () => api.listFinanceBudgets(kb, 30), []),
        loadOptional("categories", () => api.listFinanceCategories(kb), []),
        loadOptional("recurrences", () => api.listFinanceRecurrences(kb), []),
        loadOptional("rule-groups", () => api.listFinanceRuleGroups(kb), []),
        loadOptional("rules", () => api.listFinanceRules(kb), []),
      ]);
      if (kb !== currentKBRef.current) return;
      setSummary(financeSummary);
      setAccounts(accountRows);
      setTransactions(txRows);
      setBudgets(budgetRows);
      setCategories(categoryRows);
      setRecurrences(recurrenceRows);
      setRuleGroups(ruleGroupRows);
      setRules(ruleRows);
      const seeders = formSeedersRef.current;
      if (seeders) {
        const firstAsset =
          accountRows.find((a) => a.account_type === "asset") || accountRows[0];
        if (firstAsset) {
          seeders.setTxForm((prev) => (prev.account_id ? prev : { ...prev, account_id: firstAsset.id }));
          seeders.setRecurrenceForm((prev) =>
            prev.source_id ? prev : { ...prev, source_id: firstAsset.id },
          );
        }
        if (ruleGroupRows.length) {
          seeders.setRuleForm((prev) =>
            prev.rule_group_id ? prev : { ...prev, rule_group_id: ruleGroupRows[0].id },
          );
        }
      }
    } catch (err) {
      if (kb !== currentKBRef.current) return;
      console.error(err);
      setError(errMessage(err, "Could not load finance data."));
    } finally {
      if (kb === currentKBRef.current) setLoading(false);
    }
  }, [currentKB, formSeedersRef]);

  // loading/error are already reset here (initial state / the KB block
  // above); every other setState in refresh runs after an await.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- only async setState reaches here
    refresh();
  }, [refresh]);

  return {
    workspace,
    summary,
    accounts,
    transactions,
    budgets,
    categories,
    recurrences,
    ruleGroups,
    rules,
    searchResult,
    setSearchResult,
    report,
    setReport,
    currency,
    setCurrency,
    loading,
    busy,
    setBusy,
    error,
    setError,
    refresh,
    assetAccounts,
    expenseAccounts,
    revenueAccounts,
  };
}

export type FinanceWorkspaceState = ReturnType<typeof useFinanceWorkspace>;
