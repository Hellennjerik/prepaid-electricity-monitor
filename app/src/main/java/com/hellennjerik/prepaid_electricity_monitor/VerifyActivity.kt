package com.hellennjerik.prepaid_electricity_monitor

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class VerifyActivity : AppCompatActivity() {

    private lateinit var btnResend: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_verify)

        val email = intent.getStringExtra("EMAIL") ?: ""
        findViewById<TextView>(R.id.tvVerifyInfo).text = "We sent a 6-digit code to $email. It expires in 10 minutes."

        val etCode = findViewById<TextInputEditText>(R.id.etCode)
        val btnVerify = findViewById<Button>(R.id.btnVerify)
        btnResend = findViewById(R.id.btnResend)
        startResendCountdown()

        btnVerify.setOnClickListener {
            val code = etCode.text.toString().trim()
            if (code.length != 6) return@setOnClickListener toast("Enter the 6-digit code")

            btnVerify.isEnabled = false
            lifecycleScope.launch {
                try {
                    val response = ApiClient.api.verify(VerifyRequest(email, code))
                    val body = response.body()
                    if (response.isSuccessful && body != null) {
                        SessionStore.save(body.token, body.user)
                        toast("Welcome, ${body.user.full_name}!")
                        val next = Intent(this@VerifyActivity, DashboardActivity::class.java)
                        next.putExtra("METER_NUMBER", SessionStore.currentMeter)
                        next.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
                        startActivity(next)
                    } else {
                        toast(response.errorText("Could not verify the code"))
                    }
                } catch (e: Exception) {
                    toast("Cannot reach the server. Check your internet connection.")
                } finally {
                    btnVerify.isEnabled = true
                }
            }
        }

        btnResend.setOnClickListener {
            lifecycleScope.launch {
                try {
                    val response = ApiClient.api.resendCode(ResendRequest(email))
                    toast(if (response.isSuccessful) "A new code is on its way" else response.errorText())
                    if (response.isSuccessful) startResendCountdown()
                } catch (e: Exception) {
                    toast("Cannot reach the server. Check your internet connection.")
                }
            }
        }
    }

    private fun startResendCountdown() {
        btnResend.isEnabled = false
        lifecycleScope.launch {
            for (s in 30 downTo 1) {
                btnResend.text = "Resend code in ${s}s"
                delay(1000)
            }
            btnResend.text = "Resend code"
            btnResend.isEnabled = true
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}