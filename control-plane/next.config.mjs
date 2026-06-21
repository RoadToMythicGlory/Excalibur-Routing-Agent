/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_MATRIX_API:
      process.env.NEXT_PUBLIC_MATRIX_API ?? "http://127.0.0.1:8800",
  },
};

export default nextConfig;
