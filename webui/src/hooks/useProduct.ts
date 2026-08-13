import { useQuery } from '@tanstack/react-query'
import { productApi, systemApi } from '@/lib/api'

export function useCapabilities() {
  return useQuery({ queryKey: ['capabilities'], queryFn: async () => (await systemApi.capabilities()).data, staleTime: 30_000 })
}

export function useDashboardOverview() {
  return useQuery({ queryKey: ['dashboard-overview'], queryFn: async () => (await productApi.overview()).data, refetchInterval: 10_000 })
}
