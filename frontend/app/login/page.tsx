"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function LoginPage() {
  return (
    <main className="min-h-screen bg-background px-6 py-12">
      <div className="mx-auto grid max-w-6xl gap-10 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="flex flex-col justify-center">
          <p className="text-sm font-medium text-primary">DocuParse</p>
          <h1 className="mt-3 text-4xl font-semibold tracking-normal">제조업 문서 업무를 이어서 처리하세요.</h1>
          <p className="mt-4 max-w-xl text-muted-foreground">
            발주서, 견적서, 거래명세서, 납품서의 검토 필요 항목을 확인하고 ERP/엑셀 입력용 데이터로 확정하세요.
          </p>
        </section>
        <Card className="border-border/80 bg-white">
          <CardHeader>
            <CardTitle>로그인</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <label className="grid gap-2 text-sm font-medium">
              이메일
              <Input type="email" placeholder="you@company.com" />
            </label>
            <label className="grid gap-2 text-sm font-medium">
              비밀번호
              <Input type="password" placeholder="비밀번호를 입력하세요" />
            </label>
            <Button className="w-full">계속</Button>
            <p className="text-sm text-muted-foreground">
              처음이신가요?{" "}
              <Link href="/signup" className="text-foreground underline-offset-4 hover:underline">
                계정 만들기
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
