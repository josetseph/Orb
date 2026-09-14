"use client";

import { useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useKB } from "@/lib/kb-context";
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
  const { currentKB, currentKBName } = useKB();
  const formSeedersRef = useRef<FormSeeders | null>(null);
  const [tab, setTab] = useState<TabId>("overview");

  const ws = useFinanceWorkspace(currentKB, formSeedersRef);
  const mut = useFinanceMutations(ws, currentKB, formSeedersRef);

  return (
    <div className="screen flex-col overflow-auto">
      <FinanceHeader
        meta={`Firefly III · ${ws.workspace?.administration_title || currentKBName}`}
      />

      <div className="flex flex-col gap-4 px-7 pb-8">
        {ws.error && (
          <p className="rounded-md border border-danger/40 px-3 py-2 text-[12.5px] text-danger-text">
            {ws.error}
          </p>
        )}

        {ws.loading ? (
          <div className="flex items-center gap-2 text-[12.5px] text-n-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
          </div>
        ) : ws.workspace?.status === "kb_disabled" ? (
          <FinanceDisabled kbName={currentKBName} />
        ) : !ws.workspace?.ready ? (
          <FinanceNotReady
            workspace={ws.workspace}
            currency={ws.currency}
            onCurrencyChange={ws.setCurrency}
            onSubmit={mut.setPrimaryCurrency}
            onRefresh={ws.refresh}
            busy={ws.busy}
          />
        ) : (
          <>
            <FinanceWorkspaceBar
              workspace={ws.workspace}
              currentKB={currentKBName}
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
          </>
        )}
      </div>
    </div>
  );
}
