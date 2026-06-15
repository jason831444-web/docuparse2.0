import type { NextConfig } from "next";

const backendInternalUrl = process.env.DOCUPARSE_BACKEND_INTERNAL_URL ?? "http://localhost:8001";

const nextConfig: NextConfig = {
  outputFileTracingRoot: __dirname,
  images: {
    remotePatterns: [{ protocol: "http", hostname: "localhost", port: "8000" }]
  },
  async rewrites() {
    return [
      {
        source: "/api/health",
        destination: `${backendInternalUrl}/health`,
      },
      {
        source: "/api/uploads/:path*",
        destination: `${backendInternalUrl}/uploads/:path*`,
      },
      {
        source: "/api/:path*",
        destination: `${backendInternalUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
