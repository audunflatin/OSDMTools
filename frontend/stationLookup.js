const SPARQL_URL = 'https://query.wikidata.org/sparql';

const STATION_TYPES = [
  'wd:Q55488',    // railway station
  'wd:Q2175765',  // train station
  'wd:Q27020748', // railway halt
  'wd:Q4663385',  // railway stop
  'wd:Q928830',   // metro station
  'wd:Q1339195',  // underground station
].join(' ');

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function escapeSparqlString(s) {
  return s.replace(/\\/g,'\\\\').replace(/"/g,'\\"');
}

function buildUicQuery(uic) {
  return `SELECT ?station ?stationLabel ?countryLabel ?uic WHERE {
  ?station wdt:P722 "${escapeSparqlString(uic)}" .
  OPTIONAL { ?station wdt:P17 ?country. }
  BIND("${escapeSparqlString(uic)}" AS ?uic)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,no,de,sv,fr". }
} LIMIT 10`;
}

function buildNameQuery(name) {
  const esc = escapeSparqlString(name);
  return `SELECT DISTINCT ?station ?stationLabel ?countryLabel ?uic WHERE {
  SERVICE wikibase:mwapi {
    bd:serviceParam wikibase:endpoint "www.wikidata.org";
                    wikibase:api "EntitySearch";
                    mwapi:search "${esc}";
                    mwapi:language "en";
                    mwapi:limit "40".
    ?station wikibase:apiOutputItem mwapi:item.
  }
  ?station wdt:P31 ?type.
  VALUES ?type { ${STATION_TYPES} }
  OPTIONAL { ?station wdt:P17 ?country. }
  OPTIONAL { ?station wdt:P722 ?uic. }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,no,de,sv,fr". }
} LIMIT 25`;
}

async function doSearch() {
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;

  const statusEl  = document.getElementById('statusMsg');
  const tableEl   = document.getElementById('resultsTable');
  const bodyEl    = document.getElementById('resultsBody');
  const btn       = document.getElementById('searchBtn');

  statusEl.textContent = t('station_searching');
  statusEl.style.display = 'block';
  statusEl.classList.remove('is-error');
  tableEl.style.display = 'none';
  btn.disabled = true;

  try {
    const sparql = /^\d{5,8}$/.test(q) ? buildUicQuery(q) : buildNameQuery(q);
    const url = SPARQL_URL + '?query=' + encodeURIComponent(sparql) + '&format=json';
    const resp = await fetch(url, { headers: { Accept: 'application/sparql-results+json' } });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    renderResults(data.results.bindings, bodyEl, tableEl, statusEl);
  } catch {
    statusEl.textContent = t('station_error');
    statusEl.classList.add('is-error');
  } finally {
    btn.disabled = false;
  }
}

function renderResults(rows, bodyEl, tableEl, statusEl) {
  if (!rows.length) {
    statusEl.textContent = t('station_no_results');
    statusEl.classList.remove('is-error');
    return;
  }

  statusEl.style.display = 'none';

  bodyEl.innerHTML = rows.map((r, i) => {
    const name    = r.stationLabel ? escapeHtml(r.stationLabel.value) : '–';
    const country = r.countryLabel ? escapeHtml(r.countryLabel.value) : '–';
    const uic     = r.uic          ? escapeHtml(r.uic.value)          : '–';
    const wdId    = r.station      ? r.station.value.split('/').pop()  : '';
    return `<tr>
      <td>${name}</td>
      <td class="muted">${country}</td>
      <td class="muted mono">${uic}</td>
      <td class="right">
        ${wdId ? `<a href="https://www.wikidata.org/wiki/${escapeHtml(wdId)}" target="_blank" rel="noopener" class="link-external">Wikidata ↗</a>` : ''}
      </td>
    </tr>`;
  }).join('');

  tableEl.style.display = 'table';
}
