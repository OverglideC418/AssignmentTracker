export type Assignment = {
  id: number
  source_id?: number | null
  uid?: string | null
  kind: 'imported' | 'custom'
  title: string
  description: string
  start_at: string
  due_at: string
  all_day: boolean
  completed: boolean
  source_name: string
  source_color: string
  source_type: string
}

export type Source = {
  id: number
  name: string
  color: string
  enabled: boolean
  filter_rules: { include?: string[]; exclude?: string[]; overrides?: Record<string, string> }
  last_sync?: string
  last_success?: string
  last_error?: string
}

export type Preview = {
  include: Array<Assignment & { category: string }>
  review: Array<Assignment & { category: string }>
  exclude: Array<Assignment & { category: string }>
}
