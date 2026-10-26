/*
 * ExtDataStore -- read and write the Ext Data template's records from Teneo (Groovy).
 *
 * Talks to Azure Table Storage over its REST API with a SAS token, using only java.net and
 * Groovy's built-in JSON, so there is nothing to install. It reads and writes exactly what
 * store.py (the Streamlit app) does:
 *
 *     collection  -> PartitionKey      e.g. "customers"
 *     record id   -> RowKey            e.g. "C001"
 *     record      -> the "json" property: the whole record as JSON text
 *
 * In a Teneo solution this file is an ENGINE SCRIPT on the event "onsolutionloadedsecret"
 * (it reads secrets). The tools in the "Ext Data" toolkit then call ExtData.store().
 *
 * Secrets (solution scope):
 *     ext_data_account_url   https://<account>.table.core.windows.net
 *     ext_data_sas_token     the SAS token (with or without the leading "?")
 *     ext_data_table         optional: the table (one per demo), default "extdata"
 *
 * Every method throws a RuntimeException with a readable message on failure; the toolkit
 * tools catch it and turn it into apiCallSuccessful = false plus an errorMessage.
 */
import groovy.json.JsonOutput
import groovy.json.JsonSlurperClassic

class ExtDataStore {

    static final int MAX_JSON_CHARS = 32000     // a Table Storage string property holds 32K characters
    static final String API_VERSION = "2019-02-02"

    final String accountUrl
    final String sas
    final String table
    private boolean tableChecked = false

    ExtDataStore(String accountUrl, String sasToken, String table = "extdata") {
        if (!accountUrl?.trim()) throw new RuntimeException("ext_data_account_url is not set")
        if (!sasToken?.trim()) throw new RuntimeException("ext_data_sas_token is not set")
        this.accountUrl = accountUrl.trim().replaceAll('/+$', '')
        this.sas = sasToken.trim().replaceAll('^\\?+', '')
        this.table = table?.trim() ? table.trim() : "extdata"
    }

    // ---------------------------------------------------------------- public API

    /** One record as a Map, or null when there is no such record. */
    Map get(String collection, String recordId) {
        checkKey(collection, "collection"); checkKey(recordId, "record id")
        Map r = call("GET", entityUrl(collection, recordId), null, [:])
        if (r.code == 404) return null
        if (r.code != 200) throw fail(r, "reading ${collection}/${recordId}")
        return decode((Map) new JsonSlurperClassic().parseText(r.text))
    }

    /** Every record of a collection as [id: record]. Follows Azure's paging. */
    Map<String, Map> all(String collection) {
        checkKey(collection, "collection")
        Map<String, Map> result = new TreeMap<String, Map>()
        for (Map row : query("PartitionKey eq '" + collection.replace("'", "''") + "'", null)) {
            result[(String) row.RowKey] = decode(row)
        }
        return result
    }

    /**
     * Every record of every collection in ONE request: [collection: [id: record]]. Use this when a flow needs
     * several collections at once (a customer and everything that belongs to them): it costs one round trip
     * to Azure instead of one per collection. Internal collections (names starting with "_") are left out.
     */
    Map<String, Map<String, Map>> everything() {
        Map<String, Map<String, Map>> result = new TreeMap<String, Map<String, Map>>()
        for (Map row : query(null, null)) {
            String collection = (String) row.PartitionKey
            if (collection.startsWith("_")) continue
            if (!result.containsKey(collection)) result[collection] = new TreeMap<String, Map>()
            result[collection][(String) row.RowKey] = decode(row)
        }
        return result
    }

    /** Names of all collections that hold at least one record. */
    List<String> collections() {
        Set<String> names = new TreeSet<String>()
        for (Map row : query(null, "PartitionKey")) names.add((String) row.PartitionKey)
        return new ArrayList<String>(names)
    }

    /** Create or completely replace one record. Pass the WHOLE record (change what get() gave you). */
    void put(String collection, String recordId, Map record) {
        checkKey(collection, "collection"); checkKey(recordId, "record id")
        String text = JsonOutput.toJson(record)
        if (text.length() > MAX_JSON_CHARS) {
            throw new RuntimeException("Record ${collection}/${recordId} is ${text.length()} characters; the limit is ${MAX_JSON_CHARS}")
        }
        String body = JsonOutput.toJson([
            PartitionKey: collection.trim(),
            RowKey      : recordId.trim(),
            json        : text,
            updated     : java.time.Instant.now().truncatedTo(java.time.temporal.ChronoUnit.SECONDS).toString(),   // e.g. 2026-10-23T14:20:00Z (plain java.time: Date.format() is not in every Groovy)
        ])
        Map r = call("PUT", entityUrl(collection, recordId), body, [:])     // insert or replace
        if (r.code == 404 && !tableChecked) {                                // table missing: create it, retry once
            ensureTable()
            r = call("PUT", entityUrl(collection, recordId), body, [:])
        }
        if (r.code != 200 && r.code != 204) throw fail(r, "writing ${collection}/${recordId}")
    }

    /** Remove one record. Returns false when it did not exist. */
    boolean delete(String collection, String recordId) {
        checkKey(collection, "collection"); checkKey(recordId, "record id")
        Map r = call("DELETE", entityUrl(collection, recordId), null, ["If-Match": "*"])
        if (r.code == 404) return false
        if (r.code != 200 && r.code != 204) throw fail(r, "deleting ${collection}/${recordId}")
        return true
    }

    // ---------------------------------------------------------------- internals

    private void ensureTable() {
        Map r = call("POST", url("Tables", ""), JsonOutput.toJson([TableName: table]), ["Prefer": "return-no-content"])
        if (r.code != 201 && r.code != 204 && r.code != 409) throw fail(r, "creating table '${table}'")   // 409 = exists
        tableChecked = true
    }

    private List<Map> query(String filter, String select) {
        List<Map> rows = []
        String continuation = ""
        while (true) {
            List<String> parts = []
            if (filter) parts << ('%24filter=' + enc(filter))
            if (select) parts << ('%24select=' + enc(select))
            if (continuation) parts << continuation
            Map r = call("GET", url("${table}()", parts.join("&")), null, [:])
            if (r.code == 404) return rows                                   // no table yet: nothing stored
            if (r.code != 200) throw fail(r, "reading")
            Map page = (Map) new JsonSlurperClassic().parseText(r.text)
            rows.addAll((List<Map>) (page.value ?: []))
            if (!r.nextPartitionKey) return rows
            continuation = "NextPartitionKey=" + enc((String) r.nextPartitionKey)
            if (r.nextRowKey) continuation += "&NextRowKey=" + enc((String) r.nextRowKey)
        }
    }

    private static Map decode(Map entity) {
        String text = entity.json ?: "{}"
        return (Map) new JsonSlurperClassic().parseText(text)
    }

    private String url(String path, String query) {
        List<String> parts = []
        if (query) parts << query
        parts << sas
        return "${accountUrl}/${path}?${parts.join('&')}".toString()
    }

    private String entityUrl(String collection, String recordId) {
        // Quotes inside a key are doubled, then the key is percent-encoded.
        return url("${table}(PartitionKey='${keyPart(collection)}',RowKey='${keyPart(recordId)}')", "")
    }

    // URLEncoder turns a space into "+", which is wrong in a URL path: use %20.
    private static String enc(String value) {
        return URLEncoder.encode(value, "UTF-8").replace("+", "%20")
    }

    private static String keyPart(String value) {
        return enc(value.trim().replace("'", "''"))
    }

    private static void checkKey(String value, String what) {
        if (value == null || value.trim().isEmpty()) throw new RuntimeException("${what} must not be empty")
        for (char c : value.toCharArray()) {
            if (c == (char) '/' || c == (char) '\\' || c == (char) '#' || c == (char) '?' || Character.isISOControl(c)) {
                throw new RuntimeException("${what} '${value}' contains a character that is not allowed (/ \\ # ? or a control character)")
            }
        }
    }

    private Map call(String method, String address, String body, Map headers) {
        HttpURLConnection c = (HttpURLConnection) new URL(address).openConnection()
        c.setRequestMethod(method)
        c.setConnectTimeout(8000)
        c.setReadTimeout(15000)
        c.setRequestProperty("Accept", "application/json;odata=nometadata")
        c.setRequestProperty("x-ms-version", API_VERSION)
        c.setRequestProperty("DataServiceVersion", "3.0")
        headers.each { k, v -> c.setRequestProperty((String) k, (String) v) }
        try {
            if (body != null) {
                c.setDoOutput(true)
                c.setRequestProperty("Content-Type", "application/json")
                c.getOutputStream().withWriter("UTF-8") { it << body }
            }
            int code = c.getResponseCode()
            InputStream stream = code >= 400 ? c.getErrorStream() : c.getInputStream()
            String text = stream != null ? stream.getText("UTF-8") : ""
            return [code             : code,
                    text             : text,
                    nextPartitionKey : c.getHeaderField("x-ms-continuation-NextPartitionKey"),
                    nextRowKey       : c.getHeaderField("x-ms-continuation-NextRowKey")]
        } catch (IOException e) {
            throw new RuntimeException("Cannot reach Azure Table Storage at ${accountUrl}: ${e.message}")
        } finally {
            c.disconnect()
        }
    }

    private static RuntimeException fail(Map r, String action) {
        String detail = r.text
        try {
            detail = new JsonSlurperClassic().parseText((String) r.text)?."odata.error"?.message?.value ?: r.text
        } catch (Exception ignored) { }
        String hint = r.code == 403 ? " (the SAS token is wrong, expired, or lacks permission for this operation)" : ""
        return new RuntimeException("Azure ${action} failed with HTTP ${r.code}${hint}: ${detail}")
    }
}

/**
 * The shared instance for the solution, built from the solution secrets the first time it is used.
 * ExtData.store() is what the "Ext Data" toolkit tools call.
 */
class ExtData {
    private static ExtDataStore instance

    static synchronized ExtDataStore store() {
        if (instance == null) {
            instance = new ExtDataStore(
                Secrets.getSecret("ext_data_account_url"),      // these two must exist: Teneo says "Unknown secret name" if not
                Secrets.getSecret("ext_data_sas_token"),
                optionalSecret(["ext_data_table", "EXT_DATA_TABLE"], "extdata"))      // either spelling; secret names are case-sensitive
        }
        return instance
    }

    // Secrets.getSecret throws for a secret that does not exist, so optional ones are wrapped.
    // Tries each name in turn and falls back when none exists.
    private static String optionalSecret(List<String> names, String fallback) {
        for (String name : names) {
            try {
                String value = Secrets.getSecret(name)
                if (value) return value
            } catch (Exception ignored) { }
        }
        return fallback
    }

    /** Forget the cached instance (e.g. after changing the secrets, without reloading the solution). */
    static synchronized void reset() { instance = null }
}
