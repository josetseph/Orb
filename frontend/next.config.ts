import type { NextConfig } from "next";

// Desktop prepare-dist sets API_PROXY_TARGET=http://127.0.0.1:8000.
// Docker compose still uses service hostnames when env is unset in containers.
const apiProxyTarget =
  process.env.API_PROXY_TARGET ??
  (process.env.NODE_ENV === "development"
    ? "http://127.0.0.1:17401"
    : "http://backend:8000");

const filesProxyTarget =
  process.env.FILES_PROXY_TARGET ??
  (process.env.NODE_ENV === "development"
    ? "http://localhost:9000"
    : "http://rustfs:9000");

const nextConfig: NextConfig = {
  reactCompiler: true,
  output: "standalone",
  images: {
    unoptimized: true,
  },
  // Desktop uploads go through Next rewrites; default 10MB truncates and hangs.
  experimental: {
    proxyClientMaxBodySize: "512mb",
  },
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiProxyTarget}/api/v1/:path*`,
      },
      {
        source: "/vault-files/:path*",
        destination: `${apiProxyTarget}/vault-files/:path*`,
      },
      {
        source: "/health",
        destination: `${apiProxyTarget}/health`,
      },
      {
        source: "/files/:path*",
        destination: `${filesProxyTarget}/:path*`,
      },
    ];
  },
};

export default nextConfig;
