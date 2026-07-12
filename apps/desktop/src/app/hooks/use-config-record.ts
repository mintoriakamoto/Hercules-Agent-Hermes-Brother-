import { useQuery } from '@tanstack/react-query'

import { getHerculesConfigRecord } from '@/hercules'
import { queryClient, writeCache } from '@/lib/query-client'
import type { HerculesConfigRecord } from '@/types/hercules'

// One shared cache for the whole profile config record (`GET /api/config`).
// Every settings surface (MCP, model, config) reads and writes through this key
// so a save in one shows in the others, and revisiting a tab paints the cache
// instead of blanking on a fresh fetch.
//
// Distinct from session/hooks/use-hercules-config.ts, which is side-effecting —
// it pushes personality/cwd/voice/… into the session stores for live chat.
export const HERCULES_CONFIG_KEY = ['hercules-config-record'] as const

// staleTime 0 → serve cache instantly, background-revalidate on every mount.
export const useHerculesConfigRecord = () =>
  useQuery({ queryKey: HERCULES_CONFIG_KEY, queryFn: getHerculesConfigRecord, staleTime: 0 })

export const setHerculesConfigCache = writeCache<HerculesConfigRecord>(HERCULES_CONFIG_KEY)

export const invalidateHerculesConfig = () => queryClient.invalidateQueries({ queryKey: HERCULES_CONFIG_KEY })
