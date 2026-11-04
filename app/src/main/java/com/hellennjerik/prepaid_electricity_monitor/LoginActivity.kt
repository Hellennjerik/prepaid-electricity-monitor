package com.hellennjerik.prepaid_electricity_monitor

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Button
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

        val etEmail = findViewById<TextInputEditText>(R.id.etLoginEmail)
        val etPassword = findViewById<TextInputEditText>(R.id.etLoginPassword)
        val btnLogin = findViewById<Button>(R.id.btnLogin)

        findViewById<Button>(R.id.btnGoToRegister).setOnClickListener {
            startActivity(Intent(this, RegisterActivity::class.java))
        }

        btnLogin.setOnClickListener {
            val email = etEmail.text.toString().trim()
            val password = etPassword.text.toString()
            if (email.isEmpty() || password.isEmpty()) {
                return@setOnClickListener toast("Please enter your email and password")
            }

            btnLogin.isEnabled = false
            btnLogin.text = "Logging in..."
            lifecycleScope.launch {
                try {
                    val response = ApiClient.api.signIn(SignInRequest(email, password))
                    val body = response.body()
                    when {
                        response.isSuccessful && body != null -> {
                            SessionStore.save(body.token, body.user)
                            toast("Welcome back, ${body.user.full_name}!")
                            openDashboard()
                        }
                        response.code() == 403 && needsVerification(response.errorBody()?.string()) -> {
                            toast("Please verify your email first")
                            startActivity(Intent(this@LoginActivity, VerifyActivity::class.java).putExtra("EMAIL", email.lowercase()))
                        }
                        else -> toast(response.errorText("Could not log in"))
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

    private fun needsVerification(body: String?): Boolean =
        try { JSONObject(body ?: "").optBoolean("needs_verification") } catch (e: Exception) { false }

    private fun openDashboard() {
        val next = Intent(this, DashboardActivity::class.java)
        next.putExtra("METER_NUMBER", SessionStore.currentMeter)
        startActivity(next)
        finish()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}