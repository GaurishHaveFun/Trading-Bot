import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Pin the workspace root to this directory — there's an unrelated
  // lockfile higher up in the user's home directory that Next.js was
  // otherwise inferring as the root.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
