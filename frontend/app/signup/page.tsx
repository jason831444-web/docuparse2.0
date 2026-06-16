"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function SignupPage() {
  return (
    <main className="min-h-screen bg-background px-6 py-12">
      <div className="mx-auto grid max-w-6xl gap-10 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="flex flex-col justify-center">
          <p className="text-sm font-medium text-primary">제조업 문서 자동화</p>
          <h1 className="mt-3 text-4xl font-semibold tracking-normal">Docparse 작업공간을 만드세요.</h1>
          <p className="mt-4 max-w-xl text-muted-foreground">
            구매/납품 문서를 업로드하고, AI 추출 결과를 검토한 뒤 업무데이터/엑셀 입력용 데이터로 확정하세요.
          </p>
        </section>
        <Card className="border-border/80 bg-white">
          <CardHeader>
            <CardTitle>회원가입</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <label className="grid gap-2 text-sm font-medium">
              이름
              <Input placeholder="이름" />
            </label>
            <label className="grid gap-2 text-sm font-medium">
              이메일
              <Input type="email" placeholder="you@company.com" />
            </label>
            <label className="grid gap-2 text-sm font-medium">
              비밀번호
              <Input type="password" placeholder="비밀번호 만들기" />
            </label>
            <Button className="w-full">계정 만들기</Button>
            <p className="text-sm text-muted-foreground">
              이미 계정이 있으신가요?{" "}
              <Link href="/login" className="text-foreground underline-offset-4 hover:underline">
                로그인
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
