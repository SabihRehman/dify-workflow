const { randomUUID } = require('node:crypto')
const { loadAgents, saveAgents, resolveAgent } = require('../lib/diffyAgents')

// Asks Dify itself whether the caller is the workspace owner, by replaying the
// caller's own console session (cookie + CSRF header) against Dify's API over
// the internal network. Dify makes the decision; nothing here is trusted from
// the client except the credentials Dify will verify. Disabled unless
// DIFY_INTERNAL_API_URL is set (e.g. http://api:5001 in the docker stack).
async function isDifyWorkspaceOwner(request) {
  const base = (process.env.DIFY_INTERNAL_API_URL || '').replace(/\/+$/, '')
  const cookie = request.headers.cookie
  if (!base || !cookie) return false

  const headers = { cookie }
  const csrf = request.headers['x-csrf-token']
  if (csrf) headers['x-csrf-token'] = csrf

  try {
    const res = await fetch(`${base}/console/api/workspaces/current/summary`, {
      headers,
      signal: AbortSignal.timeout(5000),
    })
    if (!res.ok) return false
    const data = await res.json()
    return data?.role === 'owner'
  } catch {
    return false
  }
}

// Allowed: the admin password (Bearer), or a logged-in Dify workspace owner.
async function checkAuth(request, reply) {
  const password = process.env.ADMIN_PASSWORD
  const auth = request.headers.authorization
  if (password && auth === `Bearer ${password}`) return true

  if (await isDifyWorkspaceOwner(request)) return true

  reply.code(401).send({ error: 'Unauthorized' })
  return false
}

const MAX_SESSION_RESET_HOURS = 8760 // one year

// Optional per-agent "start a new chat after N hours of inactivity". Unset
// (null) means the Dify web app uses its own default; 0 turns the reset off.
// Returns { provided: false } when the field was not sent at all, so an edit
// can leave it alone, and { error } for an invalid value.
function parseSessionResetHours(raw) {
  if (raw === undefined) return { provided: false }
  if (raw === null || raw === '') return { provided: true, value: null }

  const value = Number(raw)
  if (!Number.isInteger(value) || value < 0 || value > MAX_SESSION_RESET_HOURS) {
    return {
      error: `session_reset_hours must be a whole number from 0 to ${MAX_SESSION_RESET_HOURS}`,
    }
  }
  return { provided: true, value }
}

// The Dify chat page reads this query parameter to decide when to start a new
// conversation. Added here so every page that shows the iframe gets it.
function withSessionResetHours(iframeUrl, hours) {
  if (hours === null || hours === undefined) return iframeUrl
  try {
    const url = new URL(iframeUrl)
    url.searchParams.set('session_reset_hours', String(hours))
    return url.toString()
  } catch {
    return iframeUrl
  }
}

async function diffyIframeRoutes(app) {
  // Public — resolves which Diffy agent's iframe URL applies to this page.
  // No message proxying: the browser loads iframe_url directly from here on.
  app.post('/diffy-iframe/check', async (request, reply) => {
    const { agent, hostname, pageUrl } = resolveAgent(request)
    if (!agent) {
      return reply.code(404).send({
        error: 'No Diffy agent configured for this page',
        hostname,
        page_url: pageUrl,
      })
    }
    const hours = agent.session_reset_hours ?? null
    return {
      ok: true,
      name: agent.name,
      iframe_url: withSessionResetHours(agent.iframe_url, hours),
      session_reset_hours: hours,
    }
  })

  // Admin CRUD for page -> Diffy iframe URL mappings
  app.get('/admin/diffy-agents', async (request, reply) => {
    if (!(await checkAuth(request, reply))) return
    return { agents: loadAgents() }
  })

  app.post('/admin/diffy-agents', async (request, reply) => {
    if (!(await checkAuth(request, reply))) return

    const {
      hostname: rawHostname,
      name,
      iframe_url: rawIframeUrl,
      session_reset_hours: rawHours,
    } = request.body || {}

    if (!rawHostname || !name || !rawIframeUrl) {
      return reply.code(400).send({ error: 'hostname, name, and iframe_url are required' })
    }

    const hostname = rawHostname.trim()
    const iframe_url = rawIframeUrl.trim()

    if (!/^https?:\/\//i.test(iframe_url)) {
      return reply.code(400).send({ error: 'iframe_url must be a full http(s) URL' })
    }

    const hours = parseSessionResetHours(rawHours)
    if (hours.error) return reply.code(400).send({ error: hours.error })

    const agents = loadAgents()
    const existing = agents.find((a) => a.hostname === hostname)
    if (existing) {
      return reply.code(409).send({ error: 'A Diffy agent for this hostname/URL already exists' })
    }

    const agent = {
      id: randomUUID(),
      hostname,
      name,
      iframe_url,
      session_reset_hours: hours.value ?? null,
    }
    agents.push(agent)
    saveAgents(agents)

    return { agent }
  })

  app.put('/admin/diffy-agents/:id', async (request, reply) => {
    if (!(await checkAuth(request, reply))) return

    const agents = loadAgents()
    const index = agents.findIndex((a) => a.id === request.params.id)
    if (index === -1) {
      return reply.code(404).send({ error: 'Agent not found' })
    }

    const {
      hostname: rawHostname,
      name,
      iframe_url: rawIframeUrl,
      session_reset_hours: rawHours,
    } = request.body || {}

    if (rawIframeUrl !== undefined && !/^https?:\/\//i.test(rawIframeUrl.trim())) {
      return reply.code(400).send({ error: 'iframe_url must be a full http(s) URL' })
    }

    const hours = parseSessionResetHours(rawHours)
    if (hours.error) return reply.code(400).send({ error: hours.error })

    if (rawHostname) agents[index].hostname = rawHostname.trim()
    if (hours.provided) agents[index].session_reset_hours = hours.value
    if (name) agents[index].name = name
    if (rawIframeUrl) agents[index].iframe_url = rawIframeUrl.trim()

    saveAgents(agents)
    return { agent: agents[index] }
  })

  app.delete('/admin/diffy-agents/:id', async (request, reply) => {
    if (!(await checkAuth(request, reply))) return

    const agents = loadAgents()
    const index = agents.findIndex((a) => a.id === request.params.id)
    if (index === -1) {
      return reply.code(404).send({ error: 'Agent not found' })
    }

    agents.splice(index, 1)
    saveAgents(agents)
    return { deleted: true }
  })
}

module.exports = diffyIframeRoutes
