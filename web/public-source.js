// Only references returned by a tool may authorize a link to a search result.
export function publicSourceUrl(hit) {
  if (hit.kind !== 'web' || typeof hit.url !== 'string' || /[\s\\]/.test(hit.url)) return '';
  let url;
  try { url = new URL(hit.url); } catch { return ''; }
  if (url.protocol !== 'https:' || url.username || url.password || url.port) return '';
  const host = url.hostname;
  if (/^https:\/\/en\.wikipedia\.org\/\?curid=\d+$/.test(url.href) ||
      /^https:\/\/api\.open-meteo\.com\/v1\/forecast\?[A-Za-z0-9%._=,&+-]+$/.test(url.href)) return url.href;
  const publishers = {'BBC News':['www.bbc.co.uk','www.bbc.com'], 'The Guardian':['www.theguardian.com'], NPR:['www.npr.org']};
  if (hit.provider === 'Publisher RSS' && Object.hasOwn(publishers,hit.publisher) &&
      publishers[hit.publisher].includes(host)) return url.href;
  if (['SearXNG','Mwmbl'].includes(hit.provider) && hit.publisher === host &&
      /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host) &&
      !/\.(local|localhost|internal|test|invalid|onion|home|lan)$/.test(host)) return url.href;
  return '';
}

export function sourceAttribution(hit) {
  return [hit.publisher, hit.published_at?.slice(0,10)].filter(Boolean).join(' · ');
}
