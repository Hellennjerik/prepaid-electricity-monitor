package com.hellennjerik.prepaid_electricity_monitor

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.lifecycle.lifecycleScope
import com.google.android.material.materialswitch.MaterialSwitch
import kotlinx.coroutines.launch

class ProfileActivity : AppCompatActivity() {

    private var alertLevel: Double? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_profile)
        setupBottomNav(R.id.nav_profile)

        // Dark mode switch (remembered on this phone)
        val prefs = getSharedPreferences("AppSettings", Context.MODE_PRIVATE)
        val switchDark = findViewById<MaterialSwitch>(R.id.switchDarkMode)
        switchDark.isChecked = prefs.getBoolean("isDarkMode", true)
        switchDark.setOnCheckedChangeListener { _, isChecked ->
            prefs.edit().putBoolean("isDarkMode", isChecked).apply()
            AppCompatDelegate.setDefaultNightMode(if (isChecked) AppCompatDelegate.MODE_NIGHT_YES else AppCompatDelegate.MODE_NIGHT_NO)
        }

        findViewById<Button>(R.id.btnEditPhone).setOnClickListener { showEditPhone() }
        findViewById<Button>(R.id.btnAddMeter).setOnClickListener { showAddMeter() }
        findViewById<Button>(R.id.btnThreshold).setOnClickListener { showEditThreshold() }
        findViewById<Button>(R.id.btnFeedback).setOnClickListener { showFeedback() }
        findViewById<Button>(R.id.btnLogout).setOnClickListener {
            SessionStore.clear()
            startActivity(Intent(this, LoginActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        }

        showProfile()
        refreshFromServer()
    }

    private fun showProfile() {
        findViewById<TextView>(R.id.tvName).text = SessionStore.fullName
        findViewById<TextView>(R.id.tvEmail).text = SessionStore.email
        findViewById<TextView>(R.id.tvPhone).text = SessionStore.phone
        findViewById<TextView>(R.id.tvThreshold).text =
            "🔔 Low-balance alert: " + (alertLevel?.let { "KSh ${it.toInt()}" } ?: "--")

        val list = findViewById<LinearLayout>(R.id.meterList)
        list.removeAllViews()
        for (m in SessionStore.meters) {
            val isCurrent = m.meter_number == SessionStore.currentMeter
            list.addView(TextView(this).apply {
                text = (if (isCurrent) "✅  " else "⚪  ") + (m.nickname?.let { "$it · " } ?: "") + m.meter_number +
                        (if (isCurrent) "   (showing)" else "")
                textSize = 16f
                minHeight = dp(52)
                gravity = android.view.Gravity.CENTER_VERTICAL
                setPadding(dp(12), 0, dp(12), 0)
                setTextColor(resources.getColor(R.color.text_primary, theme))
                setOnClickListener {
                    SessionStore.currentMeter = m.meter_number
                    showProfile()
                    toast("Now showing ${m.meter_number}")
                }
            })
        }
    }

    private fun refreshFromServer() {
        lifecycleScope.launch {
            try {
                val r = ApiClient.api.getMe()
                val me = r.body()
                if (r.isSuccessful && me != null) {
                    SessionStore.save(null, me)
                    alertLevel = me.alert_threshold
                    showProfile()
                } else if (r.code() == 401) {
                    toast("Please log in again")
                    SessionStore.clear()
                    startActivity(Intent(this@ProfileActivity, LoginActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
                }
            } catch (e: Exception) {
                // offline: keep showing what we saved
            }
        }
    }

    private fun showEditThreshold() {
        val input = EditText(this)
        input.hint = "e.g. 100"
        input.inputType = android.text.InputType.TYPE_CLASS_NUMBER
        alertLevel?.let { input.setText(it.toInt().toString()) }
        AlertDialog.Builder(this)
            .setTitle("Low-balance alert level")
            .setMessage("You will get an SMS when your balance falls to this many shillings.")
            .setView(input)
            .setPositiveButton("Save") { d, _ ->
                val level = input.text.toString().trim().toDoubleOrNull()
                d.dismiss()
                if (level == null || level < 0 || level > 10000) { toast("Enter an amount between 0 and 10,000"); return@setPositiveButton }
                lifecycleScope.launch {
                    try {
                        val r = ApiClient.api.updateMe(UpdateProfileRequest(alert_threshold = level))
                        val me = r.body()
                        if (r.isSuccessful && me != null) {
                            SessionStore.save(null, me); alertLevel = me.alert_threshold; showProfile()
                            toast("Alert level saved")
                        } else toast(r.errorText("Could not save the alert level"))
                    } catch (e: Exception) { toast("Cannot reach the server") }
                }
            }
            .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
            .show()
    }

    private fun showEditPhone() {
        val input = EditText(this)
        input.hint = "e.g. 0712345678"
        input.inputType = android.text.InputType.TYPE_CLASS_PHONE
        AlertDialog.Builder(this)
            .setTitle("Change phone number")
            .setMessage("SMS alerts will be sent to this number.")
            .setView(input)
            .setPositiveButton("Save") { d, _ ->
                val phone = input.text.toString().trim()
                d.dismiss()
                if (phone.isNotEmpty()) lifecycleScope.launch {
                    try {
                        val r = ApiClient.api.updateMe(UpdateProfileRequest(phone = phone))
                        val me = r.body()
                        if (r.isSuccessful && me != null) { SessionStore.save(null, me); showProfile(); toast("Phone number updated") }
                        else toast(r.errorText("Could not update the number"))
                    } catch (e: Exception) { toast("Cannot reach the server") }
                }
            }
            .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
            .show()
    }

    private fun showAddMeter() {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(8), dp(24), 0) }
        val etMeter = EditText(this).apply { hint = "Meter number"; inputType = android.text.InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS }
        val etName = EditText(this).apply { hint = "Nickname (e.g. Mum's house)" }
        box.addView(etMeter); box.addView(etName)
        AlertDialog.Builder(this)
            .setTitle("Add a meter")
            .setView(box)
            .setPositiveButton("Add") { d, _ ->
                val meter = etMeter.text.toString().trim().uppercase()
                val nick = etName.text.toString().trim().ifEmpty { null }
                d.dismiss()
                if (meter.isNotEmpty()) lifecycleScope.launch {
                    try {
                        val r = ApiClient.api.addMeter(AddMeterRequest(meter, nick))
                        val me = r.body()
                        if (r.isSuccessful && me != null) { SessionStore.save(null, me); showProfile(); toast("Meter added") }
                        else toast(r.errorText("Could not add the meter"))
                    } catch (e: Exception) { toast("Cannot reach the server") }
                }
            }
            .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
            .show()
    }

    private fun showFeedback() {
        val kinds = arrayOf("Something is not working", "An idea to improve the app", "Something else")
        val codes = arrayOf("bug", "idea", "other")
        var chosen = 0
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(8), dp(24), 0) }
        val spinner = android.widget.Spinner(this).apply {
            adapter = android.widget.ArrayAdapter(this@ProfileActivity, android.R.layout.simple_spinner_dropdown_item, kinds)
            onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(p: android.widget.AdapterView<*>?, v: android.view.View?, pos: Int, id: Long) { chosen = pos }
                override fun onNothingSelected(p: android.widget.AdapterView<*>?) {}
            }
        }
        val etMessage = EditText(this).apply {
            hint = "Tell us what happened or what you would like"
            minLines = 3
            maxLines = 6
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                    android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
        }
        box.addView(spinner); box.addView(etMessage)
        AlertDialog.Builder(this)
            .setTitle("Send feedback")
            .setView(box)
            .setPositiveButton("Send") { d, _ ->
                val message = etMessage.text.toString().trim()
                d.dismiss()
                if (message.length < 3) { toast("Please write a short message"); return@setPositiveButton }
                lifecycleScope.launch {
                    try {
                        val r = ApiClient.api.sendFeedback(FeedbackRequest(codes[chosen], message))
                        if (r.isSuccessful) toast("Thank you! Your feedback was sent.")
                        else toast(r.errorText("Could not send feedback"))
                    } catch (e: Exception) { toast("Cannot reach the server") }
                }
            }
            .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
            .show()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}