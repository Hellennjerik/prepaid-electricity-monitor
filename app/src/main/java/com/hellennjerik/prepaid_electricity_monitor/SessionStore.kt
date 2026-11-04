package com.hellennjerik.prepaid_electricity_monitor

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject
import retrofit2.Response

/** Remembers who is logged in (token, profile, chosen meter) across app restarts. */
object SessionStore {
    private lateinit var prefs: SharedPreferences

    fun init(context: Context) {
        if (!::prefs.isInitialized) {
            prefs = context.applicationContext.getSharedPreferences("Session", Context.MODE_PRIVATE)
        }
    }

    val token: String? get() = prefs.getString("token", null)
    val isLoggedIn: Boolean get() = !token.isNullOrEmpty()
    val fullName: String get() = prefs.getString("full_name", "") ?: ""
    val email: String get() = prefs.getString("email", "") ?: ""
    val phone: String get() = prefs.getString("phone", "") ?: ""
    val accountType: String get() = prefs.getString("account_type", "home") ?: "home"

    /** The meter currently shown on every screen. */
    var currentMeter: String
        get() = prefs.getString("current_meter", "") ?: ""
        set(v) = prefs.edit().putString("current_meter", v).apply()

    val meters: List<MeterLinkInfo>
        get() {
            val arr = JSONArray(prefs.getString("meters", "[]"))
            return (0 until arr.length()).map {
                val o = arr.getJSONObject(it)
                MeterLinkInfo(o.getString("meter_number"), o.optString("nickname").takeIf { n -> n.isNotEmpty() && n != "null" })
            }
        }

    fun save(token: String?, user: AccountProfile) {
        val arr = JSONArray()
        user.meters.forEach { arr.put(JSONObject().put("meter_number", it.meter_number).put("nickname", it.nickname ?: "")) }
        val editor = prefs.edit()
            .putString("full_name", user.full_name).putString("email", user.email)
            .putString("phone", user.phone).putString("account_type", user.account_type ?: "home")
            .putString("meters", arr.toString())
        if (token != null) editor.putString("token", token)
        val current = currentMeter
        if (current.isEmpty() || user.meters.none { it.meter_number == current }) {
            editor.putString("current_meter", user.meters.firstOrNull()?.meter_number ?: "")
        }
        editor.apply()
    }

    fun clear() = prefs.edit().clear().apply()
}

/** Turns the server's {"error": "..."} body into text for a Toast. */
fun <T> Response<T>.errorText(fallback: String = "Something went wrong"): String {
    return try {
        JSONObject(errorBody()?.string() ?: "").optString("error").ifEmpty { fallback }
    } catch (e: Exception) {
        fallback
    }
}