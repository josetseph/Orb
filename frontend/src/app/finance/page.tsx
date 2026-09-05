"use client";

import { useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useKB } from "@/lib/kb-context";
import { ShaderBackground } from "@/components/shader-background";
import {
  AccountsTab,
  BudgetsTab,
  CategoriesTab,
  FinanceDisabled,
  FinanceHeader,
  FinanceNotReady,
  FinanceTabs,
  FinanceWorkspaceBar,
  OverviewTab,
  RecurringTab,
  ReportsTab,
  RulesTab,
  SearchTab,
  TransactionsTab,
  useFinanceMutations,
  useFinanceWorkspace,
  type FormSeeders,
  type TabId,
} from "@/components/finance";

export default function FinancePage() {
  const { currentKB } = useKB();
  const formSeedersRef = useRef<FormSeeders | null>(null);
  const [tab, setTab] = useState<TabId>("overview");

  const ws = useFinanceWorkspace(currentKB, formSeedersRef);
  const mut = useFinanceMutations(ws, currentKB, formSeedersRef);

  const statusTone =
    ws.workspace?.status === "auth_mismatch"
      ? "border-amber-500/30 bg-amber-500/10 text-amber-100"
      : "border-white/10 bg-black/40 text-white/80";

  return (
    <div className="relative min-h-screen text-white">
      <ShaderBackground />
      <div className="relative z-10 mx-auto max-w-5xl px-8 py-12">
        <FinanceHeader currentKB={currentKB} />

        {ws.error && (
          <p className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-200">
            {ws.error}
          </p>
        )}

        {ws.loading ? (
          <div className="flex items-center gap-2 text-white/50">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
          </div>
        ) : ws.workspace?.status === "kb_disabled" ? (
          <FinanceDisabled kbName={currentKB} />
        ) : !ws.workspace?.ready ? (
          <FinanceNotReady
            workspace={ws.workspace}
            statusTone={statusTone}
            currency={ws.currency}
            onCurrencyChange={ws.setCurrency}
            onSubmit={mut.setPrimaryCurrency}
            onRefresh={ws.refresh}
            busy={ws.busy}
          />
        ) : (
          <div className="space-y-6">
            <FinanceWorkspaceBar
              workspace={ws.workspace}
              currentKB={currentKB}
              currency={ws.currency}
              onCurrencyChange={ws.setCurrency}
              onSubmit={mut.setPrimaryCurrency}
              onRefresh={ws.refresh}
              onReset={mut.resetAdministration}
              busy={ws.busy}
            />

            <FinanceTabs
              tab={tab}
              onSelect={(id) => {
                setTab(id);
                if (id === "reports" && !ws.report) void mut.loadReport();
              }}
            />

            {tab === "overview" && ws.summary && (
              <OverviewTab
                summary={ws.summary}
                workspace={ws.workspace}
                busy={ws.busy}
                onDeleteTransaction={mut.removeTransaction}
              />
            )}

            {tab === "accounts" && (
              <AccountsTab
                accounts={ws.accounts}
                workspace={ws.workspace}
                accountForm={mut.accountForm}
                setAccountForm={mut.setAccountForm}
                onCreate={mut.createAccount}
                busy={ws.busy}
              />
            )}

            {tab === "transactions" && (
              <TransactionsTab
                transactions={ws.transactions}
                accounts={ws.accounts}
                categories={ws.categories}
                budgets={ws.budgets}
                assetAccounts={ws.assetAccounts}
                expenseAccounts={ws.expenseAccounts}
                revenueAccounts={ws.revenueAccounts}
                workspace={ws.workspace}
                txForm={mut.txForm}
                setTxForm={mut.setTxForm}
                onCreate={mut.createTransaction}
                onDelete={mut.removeTransaction}
                busy={ws.busy}
              />
            )}

            {tab === "budgets" && (
              <BudgetsTab
                budgets={ws.budgets}
                workspace={ws.workspace}
                budgetForm={mut.budgetForm}
                setBudgetForm={mut.setBudgetForm}
                onCreate={mut.createBudget}
                busy={ws.busy}
              />
            )}

            {tab === "categories" && (
              <CategoriesTab
                categories={ws.categories}
                categoryForm={mut.categoryForm}
                setCategoryForm={mut.setCategoryForm}
                onCreate={mut.createCategory}
                onDelete={mut.removeCategory}
                busy={ws.busy}
              />
            )}

            {tab === "recurring" && (
              <RecurringTab
                recurrences={ws.recurrences}
                assetAccounts={ws.assetAccounts}
                expenseAccounts={ws.expenseAccounts}
                revenueAccounts={ws.revenueAccounts}
                workspace={ws.workspace}
                recurrenceForm={mut.recurrenceForm}
                setRecurrenceForm={mut.setRecurrenceForm}
                onCreate={mut.createRecurrence}
                onDelete={mut.removeRecurrence}
                busy={ws.busy}
              />
            )}

            {tab === "rules" && (
              <RulesTab
                ruleGroups={ws.ruleGroups}
                rules={ws.rules}
                ruleGroupForm={mut.ruleGroupForm}
                setRuleGroupForm={mut.setRuleGroupForm}
                ruleForm={mut.ruleForm}
                setRuleForm={mut.setRuleForm}
                onCreateGroup={mut.createRuleGroup}
                onCreateRule={mut.createRule}
                onDeleteGroup={mut.removeRuleGroup}
                onDeleteRule={mut.removeRule}
                busy={ws.busy}
              />
            )}

            {tab === "search" && (
              <SearchTab
                searchForm={mut.searchForm}
                setSearchForm={mut.setSearchForm}
                searchResult={ws.searchResult}
                workspace={ws.workspace}
                onSearch={mut.runSearch}
                busy={ws.busy}
              />
            )}

            {tab === "reports" && (
              <ReportsTab
                report={ws.report}
                workspace={ws.workspace}
                reportStart={mut.reportStart}
                reportEnd={mut.reportEnd}
                setReportStart={mut.setReportStart}
                setReportEnd={mut.setReportEnd}
                onLoad={mut.loadReport}
                busy={ws.busy}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
