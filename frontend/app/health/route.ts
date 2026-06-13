const backendInternalUrl = process.env.DOCUPARSE_BACKEND_INTERNAL_URL ?? "http://localhost:8001";

export async function GET() {
  const target = new URL("/health", backendInternalUrl);
  const response = await fetch(target, { cache: "no-store" });
  const responseHeaders = new Headers(response.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
}
