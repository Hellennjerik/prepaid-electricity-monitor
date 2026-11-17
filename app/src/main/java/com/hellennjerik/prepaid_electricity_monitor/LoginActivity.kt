package com.hellennjerik.prepaid_electricity_monitor

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.launch
import org.json.JSONObject

class LoginActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Apply the saved light/dark theme before the screen loads
        val isDarkMode = getSharedPreferences("AppSettings", Context.MODE_PRIVATE).getBoolean("isDarkMode", true)
        AppCompatDelegate.setDefaultNightMode(
            if (isDarkMode) AppCompatDelegate.MODE_NIGHT_YES else AppCompatDelegate.MODE_NIGHT_NO
        )

        // Already logged in on this phone? Go straight to the dashboard.
        if (SessionStore.isLoggedIn && SessionStore.currentMeter.isNotEmpty()) {
            openDashboard()
            return
        }

        setContentView(R.layout.activity_login)

        val etId = findViewById<TextInputEditText>(R.id.etLoginId)
        val etPassword = findViewById<TextInputEditText>(R.id.etLoginPassword)
        val btnLogin = findViewById<Button>(R.id.btnLogin)

        findViewById<Button>(R.id.btnGoToRegister).setOnClickListener {
            startActivity(Intent(this, RegisterActivity::class.java))
        }

        val tvServer = findViewById<TextView>(R.id.tvServer)
        fun showServer() { tvServer.text = "Server: " + ApiClient.baseUrl.removeSuffix("/") + "\nTap to change" }
        showServer()
        tvServer.setOnClickListener { showServerDialog { showServer() } }
        // Hidden developer option: long-press the "NextHome Energy" title to change the server address.
        findViewById<TextView>(R.id.tvTitle).setOnLongClickListener {
            tvServer.visibility = android.view.View.VISIBLE
            showServerDialog { showServer() }
            true
        }

        btnLogin.setOnClickListener {
            val who = etId.text.toString().trim()
            val password = etPassword.text.toString()
            if (who.isEmpty() || password.isEmpty()) {
                return@setOnClickListener toast("Enter your meter number or email, and your password")
            }

            btnLogin.isEnabled = false
            btnLogin.text = "Logging in..."
            lifecycleScope.launch {
                try {
                    val response = ApiClient.api.signIn(SignInRequest(who, password))
                    val body = response.body()
                    if (response.isSuccessful && body != null) {
                        SessionStore.save(body.token, body.user)
                        toast("Welcome back, ${body.user.full_name}!")
                        openDashboard()
                    } else {
                        toast(response.errorText("Could not log in"))
                    }
                } catch (e: Exception) {
                    toast("Cannot reach the server. Check your internet connection.")
                } finally {
                    btnLogin.isEnabled = true
                    btnLogin.text = "Log in"
                }
            }
        }
    }

    /** Lets you point the app at the hosted server, or the laptop over USB, without rebuilding the app. */
    private fun showServerDialog(onChanged: () -> Unit) {
        val input = EditText(this)
        input.hint = "https://yourname.pythonanywhere.com"
        input.setText(ApiClient.baseUrl.removeSuffix("/"))
        input.setSelection(input.text.length)
        AlertDialog.Builder(this)
            .setTitle("Server address")
            .setMessage("Where is the NextHome server?\n\nHosted: https://yourname.pythonanywhere.com\nLaptop over USB: http://127.0.0.1:5000")
            .setView(input)
            .setPositiveButton("Save") { d, _ ->
                val fixed = ApiClient.normalise(input.text.toString())
                d.dismiss()
                if (fixed == null) toast("That is not a valid address")
                else { ApiClient.baseUrl = fixed; onChanged(); toast("Server set to ${fixed.removeSuffix("/")}") }
            }
            .setNeutralButton("Laptop (USB)") { d, _ ->
                ApiClient.baseUrl = ApiClient.DEFAULT_URL; onChanged(); d.dismiss()
            }
            .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
            .show()
    }

    private fun openDashboard() {
        val next = Intent(this, DashboardActivity::class.java)
        next.putExtra("METER_NUMBER", SessionStore.currentMeter)
        startActivity(next)
        finish()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}