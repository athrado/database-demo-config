// Tool "find_records" in the "Ext Data" toolkit.
// Parameters: collection, field, value
// Outputs: records, ids, count, firstId, first, found, apiCallSuccessful, errorMessage
// Finds the records of a collection whose field equals the value (text, ignoring case).
//   - field may be nested, e.g. preferences.contact_by
//   - if the field is a list (e.g. children), it matches when any entry equals the value
//   - if the field name contains "phone", numbers are compared by their digits only, so
//     +393470000301 matches "+39 347 000 0301"
apiCallSuccessful = false
found = false
errorMessage = ""
records = [:]
ids = []
count = 0
firstId = ""
first = [:]

try {
    def wanted = (value == null) ? "" : value.toString().trim()
    if (wanted) {
        def path = field.toString().split('\\.') as List
        def phoneLike = field.toString().toLowerCase().contains("phone")
        def digits = { String s -> s.replaceAll("[^0-9]", "") }
        def same = { Object candidate ->
            if (candidate == null) return false
            if (candidate instanceof List) return candidate.any { it != null && it.toString().trim().equalsIgnoreCase(wanted) }
            def text = candidate.toString().trim()
            if (text.equalsIgnoreCase(wanted)) return true
            if (phoneLike) {
                def a = digits(text)
                return a.length() >= 7 && a == digits(wanted)
            }
            return false
        }
        ExtData.store().all(collection.toString()).each { id, rec ->
            def current = rec
            path.each { p -> current = (current instanceof Map) ? current[p] : null }
            if (same(current)) records[id] = rec
        }
    }
    ids = new ArrayList(records.keySet())
    count = records.size()
    if (count > 0) {
        found = true
        firstId = ids[0]
        first = records[firstId]
    }
    apiCallSuccessful = true
} catch (Exception e) {
    errorMessage = e.message
    println("Ext Data find failed: " + e.message)
}
