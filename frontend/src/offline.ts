export type QueuedOperation = {
  operation_id: string
  entity: 'assignment' | 'custom_task'
  entity_id: number
  action: 'complete'
  payload: { completed: boolean }
  client_changed_at: string
  device_id: string
}

const DB_NAME = 'assignmenttracker-offline'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 2)
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains('operations')) request.result.createObjectStore('operations', { keyPath: 'operation_id' })
      if (!request.result.objectStoreNames.contains('snapshots')) request.result.createObjectStore('snapshots')
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

export async function queueOperation(operation: QueuedOperation) {
  const database = await openDb()
  await new Promise<void>((resolve, reject) => {
    const request = database.transaction('operations', 'readwrite').objectStore('operations').put(operation)
    request.onsuccess = () => resolve()
    request.onerror = () => reject(request.error)
  })
}

export async function readOperations(): Promise<QueuedOperation[]> {
  const database = await openDb()
  return new Promise((resolve, reject) => {
    const request = database.transaction('operations').objectStore('operations').getAll()
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

export async function clearOperations(ids: string[]) {
  const database = await openDb()
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction('operations', 'readwrite')
    ids.forEach((id) => transaction.objectStore('operations').delete(id))
    transaction.oncomplete = () => resolve()
    transaction.onerror = () => reject(transaction.error)
  })
}

export async function cacheSnapshot(value: unknown) {
  const database = await openDb()
  await new Promise<void>((resolve, reject) => {
    const request = database.transaction('snapshots', 'readwrite').objectStore('snapshots').put(value, 'latest')
    request.onsuccess = () => resolve()
    request.onerror = () => reject(request.error)
  })
}

export async function readSnapshot<T>(): Promise<T | undefined> {
  const database = await openDb()
  return new Promise((resolve, reject) => {
    const request = database.transaction('snapshots').objectStore('snapshots').get('latest')
    request.onsuccess = () => resolve(request.result as T | undefined)
    request.onerror = () => reject(request.error)
  })
}
