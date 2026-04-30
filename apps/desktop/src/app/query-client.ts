import { QueryClient } from '@tanstack/react-query';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 10 * 60_000,
        refetchOnWindowFocus: false,
        retry: 1,
        retryDelay: (attempt) => Math.min(2_000 * 2 ** attempt, 10_000),
      },
      mutations: {
        retry: 0,
      },
    },
  });
}
