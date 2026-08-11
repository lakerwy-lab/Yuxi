import assert from 'node:assert/strict'
import test from 'node:test'

import {
  clearDingTalkLogoutRequest,
  isDingTalkLogoutRequested,
  markDingTalkLogout
} from '../../src/utils/dingtalkAuth.js'

const storage = new Map()
const sessionStorageMock = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key)
}

Object.defineProperty(globalThis, 'sessionStorage', {
  configurable: true,
  value: sessionStorageMock
})

test.after(() => {
  delete globalThis.sessionStorage
})

test('钉钉主动退出标记可被设置并清理', () => {
  clearDingTalkLogoutRequest()
  assert.equal(isDingTalkLogoutRequested(), false)

  markDingTalkLogout()
  assert.equal(isDingTalkLogoutRequested(), true)

  clearDingTalkLogoutRequest()
  assert.equal(isDingTalkLogoutRequested(), false)
})
