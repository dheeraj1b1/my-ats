const supabaseUrl = mustGet("SUPABASE_URL").replace(/\/+$/, "");
const supabaseKey = process.env.SUPABASE_ANON_KEY || process.env.SUPABASE_SERVICE_ROLE_KEY || "";
const tableName = process.env.SUPABASE_KEEPALIVE_TABLE || "";
const bucketName = process.env.SUPABASE_KEEPALIVE_BUCKET || "";
const objectPath = process.env.SUPABASE_KEEPALIVE_OBJECT || "";

const headers = supabaseKey
  ? {
      apikey: supabaseKey,
      Authorization: `Bearer ${supabaseKey}`,
    }
  : {};

const checks = [];

if (tableName) {
  checks.push({
    name: `table:${tableName}`,
    url: `${supabaseUrl}/rest/v1/${encodeURIComponent(tableName)}?select=*&limit=1`,
    options: { headers },
  });
}

if (bucketName && objectPath) {
  const cleanPath = objectPath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");

  checks.push({
    name: `storage:${bucketName}/${objectPath}`,
    url: `${supabaseUrl}/storage/v1/object/${encodeURIComponent(bucketName)}/${cleanPath}`,
    options: { method: "HEAD", headers },
  });
}

if (!checks.length) {
  checks.push({
    name: "project-api",
    url: `${supabaseUrl}/rest/v1/`,
    options: { headers },
    allowClientError: true,
  });
}

for (const check of checks) {
  const startedAt = Date.now();
  const response = await fetch(check.url, check.options);
  const elapsedMs = Date.now() - startedAt;
  const ok = response.ok || (check.allowClientError && response.status < 500);

  console.log(`${check.name}: HTTP ${response.status} in ${elapsedMs}ms`);

  if (!ok) {
    const body = await safeBody(response);
    throw new Error(`${check.name} failed with HTTP ${response.status}${body ? `: ${body}` : ""}`);
  }
}

function mustGet(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

async function safeBody(response) {
  try {
    return (await response.text()).slice(0, 500);
  } catch {
    return "";
  }
}
