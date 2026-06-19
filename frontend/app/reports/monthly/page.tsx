"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BarChart3, Download, FileSpreadsheet, RefreshCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { formatDate, formatMoney, titleCaseLabel } from "@/lib/utils";
import type { MonthlyReport, MonthlyReportIssueRow } from "@/types/document";

type ReportPeriod = "day" | "week" | "month" | "year" | "custom";

const PERIOD_LABELS: Record<ReportPeriod, string> = {
  day: "일",
  week: "주",
  month: "달",
  year: "년",
  custom: "직접 설정",
};

export default function MonthlyReportPage() {
  const initial = useMemo(() => currentMonthRange(), []);
  const [period, setPeriod] = useState<ReportPeriod>("month");
  const [startDate, setStartDate] = useState(initial.startDate);
  const [endDate, setEndDate] = useState(initial.endDate);
  const [selectedParty, setSelectedParty] = useState("");
  const [report, setReport] = useState<MonthlyReport | null>(null);
  const [partyOptionsReport, setPartyOptionsReport] = useState<MonthlyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const params = useMemo(
    () => {
      const query = new URLSearchParams({ start_date: startDate, end_date: endDate, period });
      if (selectedParty) query.set("party_name", selectedParty);
      return query;
    },
    [endDate, period, selectedParty, startDate],
  );
  const partyOptionParams = useMemo(
    () => new URLSearchParams({ start_date: startDate, end_date: endDate, period }),
    [endDate, period, startDate],
  );

  useEffect(() => {
    setLoading(true);
    api.monthlyReport(params).then(setReport).catch(() => setReport(null)).finally(() => setLoading(false));
  }, [params]);

  useEffect(() => {
    api.monthlyReport(partyOptionParams).then(setPartyOptionsReport).catch(() => setPartyOptionsReport(null));
  }, [partyOptionParams]);

  const issueRows = report ? [
    ...report.issues.missing_required_fields,
    ...report.issues.calculation_mismatches,
    ...report.issues.pending_documents,
  ] : [];
  const partyOptions = partyOptionsReport?.by_party ?? report?.by_party ?? [];

  function applyPeriod(nextPeriod: ReportPeriod) {
    setPeriod(nextPeriod);
    if (nextPeriod === "custom") return;
    const range = periodRange(nextPeriod);
    setStartDate(range.startDate);
    setEndDate(range.endDate);
  }

  return (
    <main className="shell py-7">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-primary">업무데이터 확인</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-normal text-slate-950">보고서 / 거래 통계</h1>
          <p className="mt-1 text-sm text-muted-foreground">문서 기반 거래 통계와 정산 참고용 리포트입니다. 공식 회계 장부나 세무 신고 자료가 아니라 업무데이터 확인용입니다.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-md border bg-white p-1">
            {(["day", "week", "month", "year"] as ReportPeriod[]).map((key) => (
              <Button key={key} type="button" variant={period === key ? "default" : "ghost"} size="sm" className="h-7 px-3" onClick={() => applyPeriod(key)}>
                {PERIOD_LABELS[key]}
              </Button>
            ))}
          </div>
          <Input className="h-9 w-36" type="date" value={startDate} onChange={(event) => { setPeriod("custom"); setStartDate(event.target.value); }} />
          <span className="text-sm text-muted-foreground">~</span>
          <Input className="h-9 w-36" type="date" value={endDate} onChange={(event) => { setPeriod("custom"); setEndDate(event.target.value); }} />
          <select
            className="h-9 min-w-44 rounded-md border bg-white px-3 text-sm"
            value={selectedParty}
            onChange={(event) => setSelectedParty(event.target.value)}
            aria-label="거래처 선택"
          >
            <option value="">전체 거래처</option>
            {partyOptions.map((party) => (
              <option key={party.name} value={party.name}>
                {party.name}
              </option>
            ))}
          </select>
          <Button variant="outline" size="sm" onClick={() => api.monthlyReport(params).then(setReport)}>
            <RefreshCcw className="size-4" /> 새로고침
          </Button>
          <Button asChild variant="outline" size="sm">
            <a href={api.monthlyReportExportUrl(params, "csv")}>
              <Download className="size-4" /> CSV
            </a>
          </Button>
          <Button asChild size="sm">
            <a href={api.monthlyReportExportUrl(params, "xlsx")}>
              <FileSpreadsheet className="size-4" /> Excel
            </a>
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-muted" />
      ) : report ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-white px-4 py-3 text-sm">
            <div>
              <span className="font-medium text-slate-950">집계 기간</span>
              <span className="ml-2 text-muted-foreground">{report.range_label}</span>
              {report.party_name ? <span className="ml-2 text-muted-foreground">· {report.party_name}</span> : null}
            </div>
            <Badge variant="outline" className="bg-slate-50 text-slate-700 shadow-none">{PERIOD_LABELS[(report.period as ReportPeriod) || "custom"] ?? "기간"}</Badge>
          </div>

          <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
            <MetricCard label="전체 문서 수" value={report.summary.total_documents} />
            <MetricCard label="검수 완료" value={report.summary.verified_documents} tone="emerald" />
            <MetricCard label="미검수/대기" value={report.summary.pending_documents} tone="amber" />
            <MetricCard label="총 거래 금액" value={formatMoney(report.summary.total_amount, "KRW")} />
            <MetricCard label="확인 필요 문서" value={report.summary.documents_with_errors} tone="red" />
            <MetricCard label="금액 없는 수량 문서" value={report.summary.no_price_documents ?? 0} />
          </section>

          <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <HorizontalBarChart
              title="거래처별 거래 금액 그래프"
              description={selectedParty ? "선택한 거래처 기준 집계입니다." : "금액 기준 상위 거래처를 한눈에 봅니다."}
              rows={report.by_party.slice(0, 8).map((row) => ({
                label: row.name,
                value: row.total_amount,
                displayValue: formatMoney(row.total_amount, "KRW"),
                caption: `${row.document_count}건`,
              }))}
            />
            <HorizontalBarChart
              title="품목별 금액 그래프"
              description="품목별 총 금액 기준 상위 항목입니다."
              rows={report.by_item.slice(0, 8).map((row) => ({
                label: row.spec ? `${row.item_name} · ${row.spec}` : row.item_name,
                value: row.total_amount,
                displayValue: formatMoney(row.total_amount, "KRW"),
                caption: `수량 ${row.quantity}`,
              }))}
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <StatusChart report={report} />
            <HorizontalBarChart
              title="문서 유형별 업무량 그래프"
              description="문서 유형별 처리 건수를 비교합니다."
              rows={(report.by_document_type ?? []).slice(0, 8).map((row) => ({
                label: titleCaseLabel(row.document_type),
                value: row.document_count,
                displayValue: `${row.document_count}건`,
                caption: `검수 ${row.verified_documents} · 대기 ${row.pending_documents}`,
              }))}
              valueKind="count"
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <ReportTable
              title="거래처별 거래 금액"
              description="검수 완료 문서 기준으로 집계합니다."
              headers={["거래처명", "문서 수", "총 거래 금액"]}
              rows={report.by_party.map((row) => [row.name, row.document_count, formatMoney(row.total_amount, "KRW")])}
            />
            <ReportTable
              title="문서 유형별 업무량"
              description="발주서, 납품서, 검사성적서처럼 업무 문서 유형별 처리 현황을 봅니다."
              headers={["문서 유형", "전체", "검수 완료", "미검수", "금액 없는 문서"]}
              rows={(report.by_document_type ?? []).map((row) => [
                titleCaseLabel(row.document_type),
                row.document_count,
                row.verified_documents,
                row.pending_documents,
                row.no_price_documents,
              ])}
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <ReportTable
              title="품목별 수량/금액"
              description="품명과 규격이 같은 품목을 묶어서 봅니다."
              headers={["품명", "규격", "총 수량", "총 금액"]}
              rows={report.by_item.map((row) => [row.item_name, row.spec || "-", row.quantity, formatMoney(row.total_amount, "KRW")])}
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <ReportTable
              title="거래 금액 기준 상위 거래처"
              description="기간 내 우선 확인할 거래처입니다."
              headers={["거래처명", "문서 수", "총 거래 금액"]}
              rows={report.top_parties.map((row) => [row.name, row.document_count, formatMoney(row.total_amount, "KRW")])}
            />
            <ReportTable
              title="수량/금액 기준 상위 품목"
              description="반복 구매/납품이 많은 품목을 확인합니다."
              headers={["품명", "규격", "총 수량", "총 금액"]}
              rows={report.top_items.map((row) => [row.item_name, row.spec || "-", row.quantity, formatMoney(row.total_amount, "KRW")])}
            />
          </section>

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle className="text-base">확인 필요 문서</CardTitle>
                <p className="mt-1 text-sm text-muted-foreground">누락 필드, 계산 불일치, 미검수 문서를 업무데이터 확정 전에 확인하세요.</p>
              </div>
              <Badge className="border-amber-200 bg-amber-50 text-amber-800 shadow-none">{issueRows.length}건</Badge>
            </CardHeader>
            <CardContent>
              {issueRows.length ? <IssueTable rows={issueRows} /> : <p className="rounded-lg border bg-slate-50 p-6 text-sm text-muted-foreground">확인 필요 문서가 없습니다.</p>}
            </CardContent>
          </Card>
        </div>
      ) : (
        <Card>
          <CardContent className="p-10 text-center text-muted-foreground">보고서를 불러오지 못했습니다.</CardContent>
        </Card>
      )}
    </main>
  );
}

function MetricCard({ label, value, tone }: { label: string; value: string | number; tone?: "emerald" | "amber" | "red" }) {
  return (
    <Card>
      <CardContent className="p-5">
        <p className="text-sm text-muted-foreground">{label}</p>
        <p className={`mt-5 text-2xl font-semibold leading-none ${tone === "emerald" ? "text-emerald-700" : tone === "amber" ? "text-amber-700" : tone === "red" ? "text-red-700" : "text-slate-950"}`}>{value}</p>
      </CardContent>
    </Card>
  );
}

function HorizontalBarChart({
  title,
  description,
  rows,
  valueKind = "money",
}: {
  title: string;
  description: string;
  rows: Array<{ label: string; value: number; displayValue: string; caption?: string }>;
  valueKind?: "money" | "count";
}) {
  const maxValue = Math.max(...rows.map((row) => Math.abs(Number(row.value) || 0)), 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <BarChart3 className="size-4 text-primary" />
          {title}
        </CardTitle>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        {rows.length ? rows.map((row, index) => {
          const value = Number(row.value) || 0;
          const width = maxValue ? Math.max(4, Math.round((Math.abs(value) / maxValue) * 100)) : 0;
          const negative = value < 0;
          return (
            <div key={`${row.label}-${index}`} className="grid gap-1.5">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="min-w-0 truncate font-medium text-slate-800">{row.label}</span>
                <span className={negative ? "shrink-0 font-semibold text-red-700" : "shrink-0 font-semibold text-slate-900"}>
                  {row.displayValue}
                </span>
              </div>
              <div className="h-3 overflow-hidden rounded-full bg-slate-100">
                <div
                  className={`h-full rounded-full ${negative ? "bg-red-500" : valueKind === "count" ? "bg-blue-500" : "bg-emerald-500"}`}
                  style={{ width: `${width}%` }}
                />
              </div>
              {row.caption ? <p className="text-[11px] text-muted-foreground">{row.caption}</p> : null}
            </div>
          );
        }) : (
          <p className="rounded-lg border bg-slate-50 p-6 text-sm text-muted-foreground">그래프로 표시할 데이터가 없습니다.</p>
        )}
      </CardContent>
    </Card>
  );
}

function StatusChart({ report }: { report: MonthlyReport }) {
  const rows = [
    { label: "검수 완료", value: report.summary.verified_documents, className: "bg-emerald-500" },
    { label: "미검수/대기", value: report.summary.pending_documents, className: "bg-amber-500" },
    { label: "확인 필요", value: report.summary.documents_with_errors, className: "bg-red-500" },
    { label: "금액 없는 수량 문서", value: report.summary.no_price_documents ?? 0, className: "bg-slate-500" },
  ];
  const total = Math.max(report.summary.total_documents, rows.reduce((sum, row) => sum + row.value, 0), 1);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <BarChart3 className="size-4 text-primary" />
          문서 처리 상태 그래프
        </CardTitle>
        <p className="text-sm text-muted-foreground">기간 내 문서 처리 상태를 비교합니다.</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex h-4 overflow-hidden rounded-full bg-slate-100">
          {rows.map((row) => (
            <span
              key={row.label}
              className={row.className}
              style={{ width: `${Math.max(0, Math.round((row.value / total) * 100))}%` }}
              title={`${row.label} ${row.value}건`}
            />
          ))}
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {rows.map((row) => (
            <div key={row.label} className="flex items-center justify-between rounded-md border bg-white px-3 py-2 text-sm">
              <span className="flex items-center gap-2">
                <span className={`size-2.5 rounded-full ${row.className}`} />
                {row.label}
              </span>
              <span className="font-semibold">{row.value}건</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ReportTable({ title, description, headers, rows }: { title: string; description: string; headers: string[]; rows: Array<Array<string | number>> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              {headers.map((header) => <th key={header} className="px-2 py-2 font-medium">{header}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.length ? rows.map((row, index) => (
              <tr key={index} className="border-b last:border-0">
                {row.map((cell, cellIndex) => <td key={`${index}-${cellIndex}`} className="px-2 py-2">{cell}</td>)}
              </tr>
            )) : (
              <tr><td className="px-2 py-6 text-muted-foreground" colSpan={headers.length}>집계 데이터가 없습니다.</td></tr>
            )}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function IssueTable({ rows }: { rows: MonthlyReportIssueRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            {["문서", "유형", "거래처", "날짜", "문제 유형", "설명"].map((header) => <th key={header} className="px-2 py-2 font-medium">{header}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.document_id}-${index}`} className="border-b last:border-0">
              <td className="px-2 py-2">{row.document_number || row.document_id || "문서번호 없음"}</td>
              <td className="px-2 py-2">{titleCaseLabel(row.document_type)}</td>
              <td className="px-2 py-2">{row.party_name || "거래처 미확인"}</td>
              <td className="px-2 py-2">{row.date ? formatDate(row.date) : "날짜 없음"}</td>
              <td className="px-2 py-2">
                <Badge className="border-amber-200 bg-amber-50 text-amber-800 shadow-none">
                  <AlertTriangle className="size-3" /> {row.issue_type}
                </Badge>
              </td>
              <td className="px-2 py-2 text-muted-foreground">{row.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function periodRange(period: Exclude<ReportPeriod, "custom">) {
  const today = new Date();
  if (period === "day") {
    return { startDate: formatInputDate(today), endDate: formatInputDate(today) };
  }
  if (period === "week") {
    const start = new Date(today);
    const day = start.getDay() || 7;
    start.setDate(start.getDate() - day + 1);
    const end = new Date(start);
    end.setDate(start.getDate() + 6);
    return { startDate: formatInputDate(start), endDate: formatInputDate(end) };
  }
  if (period === "year") {
    return {
      startDate: `${today.getFullYear()}-01-01`,
      endDate: `${today.getFullYear()}-12-31`,
    };
  }
  return currentMonthRange(today);
}

function currentMonthRange(source = new Date()) {
  const start = new Date(source.getFullYear(), source.getMonth(), 1);
  const end = new Date(source.getFullYear(), source.getMonth() + 1, 0);
  return { startDate: formatInputDate(start), endDate: formatInputDate(end) };
}

function formatInputDate(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
