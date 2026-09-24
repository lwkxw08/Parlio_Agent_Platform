// Cloudflare Pages Function: host-level redirects that _redirects cannot express
// (Pages only applies _redirects to paths, so www.parliotec.com served a duplicate 200).
const APEX = "parliotec.com";

export async function onRequest({ request, next }) {
  const url = new URL(request.url);
  if (url.hostname === `www.${APEX}`) {
    url.hostname = APEX;
    return Response.redirect(url.toString(), 301);
  }
  return next();
}
