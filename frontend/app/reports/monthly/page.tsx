"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Download, FileSpreadsheet, LineChart, RefreshCcw } from "lucide-react";

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

          <DateTrendChart report={report} />

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

function DateTrendChart({ report }: { report: MonthlyReport }) {
  const rows = (report.by_date ?? []).filter((row) => row.document_count || row.total_amount || row.pending_documents || row.verified_documents);
  const chartRows = rows.length ? rows : (report.by_date ?? []);
  const width = 920;
  const height = 260;
  const padding = { left: 54, right: 26, top: 26, bottom: 48 };
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const maxAmount = Math.max(...chartRows.map((row) => Number(row.total_amount) || 0), 0);
  const maxCount = Math.max(...chartRows.map((row) => Number(row.document_count) || 0), 1);
  const amountPoints = chartRows.map((row, index) => pointFor(index, chartRows.length, Number(row.total_amount) || 0, maxAmount || 1, padding, innerWidth, innerHeight));
  const countPoints = chartRows.map((row, index) => pointFor(index, chartRows.length, Number(row.document_count) || 0, maxCount, padding, innerWidth, innerHeight));
  const amountPath = buildPolylinePath(amountPoints);
  const countPath = buildPolylinePath(countPoints);
  const amountAreaPath = amountPoints.length
    ? `${amountPath} L ${amountPoints[amountPoints.length - 1].x} ${padding.top + innerHeight} L ${amountPoints[0].x} ${padding.top + innerHeight} Z`
    : "";
  const xLabels = compactDateLabels(chartRows.map((row) => row.date));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <LineChart className="size-4 text-primary" />
          날짜별 문서 처리 추이
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          선택한 기간의 날짜별 문서 수와 검수 완료 문서 금액 흐름을 함께 봅니다.
        </p>
      </CardHeader>
      <CardContent>
        {chartRows.length ? (
          <div className="overflow-x-auto">
            <svg className="min-w-[720px] rounded-lg border bg-white" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="날짜별 문서 처리 추이 그래프">
              <defs>
                <linearGradient id="amountTrendFill" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="#10b981" stopOpacity="0.26" />
                  <stop offset="100%" stopColor="#10b981" stopOpacity="0.03" />
                </linearGradient>
              </defs>
              {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
                const y = padding.top + innerHeight * ratio;
                return <line key={ratio} x1={padding.left} x2={width - padding.right} y1={y} y2={y} stroke="#e2e8f0" strokeDasharray={ratio === 1 ? "0" : "4 4"} />;
              })}
              <text x={padding.left} y={18} className="fill-slate-500 text-[11px]">금액</text>
              <text x={width - padding.right - 34} y={18} className="fill-slate-500 text-[11px]">문서 수</text>
              {amountAreaPath ? <path d={amountAreaPath} fill="url(#amountTrendFill)" /> : null}
              {amountPath ? <path d={amountPath} fill="none" stroke="#10b981" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" /> : null}
              {countPath ? <path d={countPath} fill="none" stroke="#2563eb" strokeDasharray="5 5" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" /> : null}
              {chartRows.map((row, index) => {
                const amountPoint = amountPoints[index];
                const countPoint = countPoints[index];
                return (
                  <g key={row.date}>
                    <circle cx={amountPoint.x} cy={amountPoint.y} r="4" fill="#10b981">
                      <title>{`${formatDate(row.date)} 거래금액 ${formatMoney(row.total_amount, "KRW")}`}</title>
                    </circle>
                    <circle cx={countPoint.x} cy={countPoint.y} r="3.5" fill="#2563eb">
                      <title>{`${formatDate(row.date)} 문서 ${row.document_count}건 · 검수 ${row.verified_documents}건 · 대기 ${row.pending_documents}건`}</title>
                    </circle>
                  </g>
                );
              })}
              {xLabels.map((label) => (
                <text key={label.index} x={pointFor(label.index, chartRows.length, 0, 1, padding, innerWidth, innerHeight).x} y={height - 19} textAnchor="middle" className="fill-slate-500 text-[11px]">
                  {label.text}
                </text>
              ))}
              <text x={padding.left} y={height - 7} className="fill-slate-400 text-[10px]">날짜</text>
            </svg>
            <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1"><span className="size-2.5 rounded-full bg-emerald-500" />검수 완료 거래금액</span>
              <span className="flex items-center gap-1"><span className="size-2.5 rounded-full bg-blue-600" />전체 문서 수</span>
              <span>금액 없는 수량 문서는 금액선에는 반영하지 않습니다.</span>
            </div>
          </div>
        ) : (
          <p className="rounded-lg border bg-slate-50 p-6 text-sm text-muted-foreground">날짜별로 표시할 데이터가 없습니다.</p>
        )}
      </CardContent>
    </Card>
  );
}

function pointFor(
  index: number,
  length: number,
  value: number,
  maxValue: number,
  padding: { left: number; top: number },
  innerWidth: number,
  innerHeight: number,
) {
  const x = padding.left + (length <= 1 ? innerWidth / 2 : (index / (length - 1)) * innerWidth);
  const y = padding.top + innerHeight - (maxValue ? Math.min(1, Math.max(0, value / maxValue)) * innerHeight : 0);
  return { x, y };
}

function buildPolylinePath(points: Array<{ x: number; y: number }>) {
  if (!points.length) return "";
  return points.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
}

function compactDateLabels(dates: string[]) {
  if (!dates.length) return [];
  const target = Math.min(6, dates.length);
  const step = Math.max(1, Math.floor((dates.length - 1) / Math.max(target - 1, 1)));
  const indexes = new Set<number>([0, dates.length - 1]);
  for (let index = 0; index < dates.length; index += step) indexes.add(index);
  return Array.from(indexes)
    .sort((a, b) => a - b)
    .map((index) => ({ index, text: dates[index].slice(5).replace("-", ".") }));
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
