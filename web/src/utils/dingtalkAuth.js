/** 钉钉 H5 免登：检测客户端并通过 JSAPI 获取 authCode。 */

const DINGTALK_JSAPI_TIMEOUT_MS = 5000
const DINGTALK_JSAPI_POLL_INTERVAL_MS = 50
const DINGTALK_LOGOUT_REQUEST_KEY = 'dingtalk_logout_requested'

function isDingTalkUserAgent() {
  return typeof navigator !== 'undefined' && /dingtalk/i.test(navigator.userAgent)
}

function getDingTalkAuthCodeApi(dd) {
  if (typeof dd?.runtime?.permission?.requestAuthCode === 'function') {
    return dd.runtime.permission.requestAuthCode.bind(dd.runtime.permission)
  }

  if (typeof dd?.requestAuthCode === 'function') {
    return dd.requestAuthCode.bind(dd)
  }

  return null
}

/**
 * 判断当前页面是否运行在钉钉客户端内。
 * 仅根据 User-Agent 判断客户端，JSAPI 加载状态由 requestDingTalkAuthCode 等待处理。
 */
export function isInDingTalk() {
  return isDingTalkUserAgent()
}

/** 标记用户主动退出，避免钉钉免登立即把用户重新登录。 */
export function markDingTalkLogout() {
  if (typeof sessionStorage !== 'undefined') {
    sessionStorage.setItem(DINGTALK_LOGOUT_REQUEST_KEY, '1')
  }
}

/** 判断本次登录页是否由用户主动退出触发。 */
export function isDingTalkLogoutRequested() {
  return (
    typeof sessionStorage !== 'undefined' &&
    sessionStorage.getItem(DINGTALK_LOGOUT_REQUEST_KEY) === '1'
  )
}

/** 清理主动退出标记，避免后续从钉钉入口打开时跳过免登。 */
export function clearDingTalkLogoutRequest() {
  if (typeof sessionStorage !== 'undefined') {
    sessionStorage.removeItem(DINGTALK_LOGOUT_REQUEST_KEY)
  }
}

function waitForDingTalkJSAPI() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + DINGTALK_JSAPI_TIMEOUT_MS
    let readyRegistered = false

    const check = () => {
      const requestAuthCode = getDingTalkAuthCodeApi(window.dd)
      if (requestAuthCode) {
        resolve(requestAuthCode)
        return
      }

      if (!readyRegistered && typeof window.dd?.ready === 'function') {
        readyRegistered = true
        window.dd.ready(check)
      }

      if (Date.now() >= deadline) {
        reject(new Error('钉钉 JSAPI 未就绪，请从工作台打开 H5 微应用后重试'))
        return
      }
      window.setTimeout(check, DINGTALK_JSAPI_POLL_INTERVAL_MS)
    }

    check()
  })
}

/** 从后端获取供 JSAPI 使用的 corpId/clientId。 */
export async function getDingTalkPublicConfig() {
  const response = await fetch('/api/auth/public-config')
  if (!response.ok) {
    throw new Error('获取钉钉免登配置失败')
  }
  return response.json()
}

/** 调用钉钉网页应用免登 JSAPI 获取一次性 authCode。 */
export async function requestDingTalkAuthCode(corpId, clientId) {
  const requestAuthCode = await waitForDingTalkJSAPI()

  return new Promise((resolve, reject) => {
    requestAuthCode({
      corpId,
      ...(clientId ? { clientId } : {}),
      onSuccess: (result) => {
        if (result?.code) {
          resolve(result.code)
        } else {
          reject(new Error('钉钉免登未返回 authCode'))
        }
      },
      onFail: (error) => {
        reject(new Error(`钉钉免登失败：${JSON.stringify(error)}`))
      }
    })
  })
}
