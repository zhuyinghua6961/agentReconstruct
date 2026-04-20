export async function runPersonnelManagementRefresh(fetchUsers) {
  if (typeof fetchUsers === 'function') {
    await fetchUsers()
  }
}
