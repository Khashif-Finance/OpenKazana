import path from "path";
import type { NextConfig } from "next";

const internalApi = process.env.INTERNAL_API_BASE_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname),
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${internalApi}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
