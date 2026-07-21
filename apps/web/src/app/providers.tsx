"use client";

// One TanStack Query client per mounted browser application, composed with
// the existing AuthProvider. Query data is MEMORY-ONLY: no persistence
// plugin, no localStorage/sessionStorage/IndexedDB, no server-side singleton
// (each mount gets its own QueryClient via useState so hydration never
// shares cached state across users/requests). Conservative global defaults;
// feature-specific polling/refetch rules stay with the query that needs
// them (see features/generation and features/results).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { DemoBanner } from "@/features/config/DemoBanner";
import { AuthProvider } from "@/lib/auth";

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // No background polling or focus refetch by default — a query that
        // needs either declares it explicitly (progress/results queries).
        refetchOnWindowFocus: false,
        refetchIntervalInBackground: false,
        retry: 1,
        staleTime: 0,
        gcTime: 0,
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <DemoBanner />
        {children}
      </AuthProvider>
    </QueryClientProvider>
  );
}
