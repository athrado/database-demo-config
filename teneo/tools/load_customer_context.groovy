// Tool "load_customer_context" in the "Ext Data" toolkit.
// Parameters: collection, field, value, linkField (optional, default customer_id)
// Outputs: customer, customerId, related, shared, found, apiCallSuccessful, errorMessage
// ONE request to the data store. Finds the primary record (e.g. the customer with this phone number) and returns
// everything that belongs to it:
//   related  {collection: {id: record}}  records of other collections whose linkField equals the customer's id
//                                        (accounts, alerts, orders, ...), e.g. related.alerts
//   shared   {collection: {id: record}}  collections nobody links to: catalogues and settings (loan_offers, rules, ...)
// Matching is as in find_records: ignoring case; "phone" fields are compared by digits only.
apiCallSuccessful = false
found = false
errorMessage = ""
customer = [:]
customerId = ""
related = [:]
shared = [:]

try {
    def linkName = (linkField == null || linkField.toString().trim().isEmpty()) ? "customer_id" : linkField.toString().trim()
    def wanted = (value == null) ? "" : value.toString().trim()
    def everything = ExtData.store().everything()
    def people = everything[collection.toString()] ?: [:]

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
        def hit = people.find { id, rec ->
            def current = rec
            path.each { p -> current = (current instanceof Map) ? current[p] : null }
            same(current)
        }
        if (hit) {
            customerId = hit.key
            customer = hit.value
            found = true
        }
    }

    if (found) {
        everything.each { name, recs ->
            if (name == collection.toString()) return
            def mine = recs.findAll { id, rec -> rec[linkName] == customerId }
            if (mine) {
                related[name] = mine
            } else if (!recs.values().any { it.containsKey(linkName) }) {
                shared[name] = recs
            }
        }
    }
    apiCallSuccessful = true
} catch (Exception e) {
    errorMessage = e.message
    println("Ext Data load context failed: " + e.message)
}
