package com.hellennjerik.prepaid_electricity_monitor

import android.app.DatePickerDialog
import android.content.Intent
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.RadioButton
import android.widget.Spinner
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.launch
import java.util.Calendar

class RegisterActivity : AppCompatActivity() {

    // what the user sees  ->  what the server stores
    private val genders = listOf(
        "Prefer not to say" to "prefer_not_to_say",
        "Female" to "female",
        "Male" to "male",
        "Other" to "other"
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_register)

        val etFullName = findViewById<TextInputEditText>(R.id.etFullName)
        val etEmail = findViewById<TextInputEditText>(R.id.etEmail)
        val etPhone = findViewById<TextInputEditText>(R.id.etPhone)
        val etDob = findViewById<TextInputEditText>(R.id.etDob)
        val spGender = findViewById<Spinner>(R.id.spGender)
        val rbWork = findViewById<RadioButton>(R.id.rbWork)
        val etMeter = findViewById<TextInputEditText>(R.id.etMeter)
        val etPassword = findViewById<TextInputEditText>(R.id.etPassword)
        val etConfirm = findViewById<TextInputEditText>(R.id.etConfirm)
        val btnRegister = findViewById<Button>(R.id.btnRegister)

        spGender.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, genders.map { it.first })

        // Date of birth: tap to pick, stored as YYYY-MM-DD
        etDob.setOnClickListener {
            val c = Calendar.getInstance()
            DatePickerDialog(this, { _, y, m, d ->
                etDob.setText(String.format("%04d-%02d-%02d", y, m + 1, d))
            }, c.get(Calendar.YEAR) - 25, c.get(Calendar.MONTH), c.get(Calendar.DAY_OF_MONTH)).show()
        }

        btnRegister.setOnClickListener {
            val name = etFullName.text.toString().trim()
            val email = etEmail.text.toString().trim()
            val phone = etPhone.text.toString().trim()
            val dob = etDob.text.toString().trim()
            val meter = etMeter.text.toString().trim().uppercase()
            val password = etPassword.text.toString()

            when {
                name.isEmpty() || email.isEmpty() || phone.isEmpty() || dob.isEmpty() || meter.isEmpty() || password.isEmpty() ->
                    return@setOnClickListener toast("Please fill in every field")
                password != etConfirm.text.toString() ->
                    return@setOnClickListener toast("The two passwords do not match")
            }

            btnRegister.isEnabled = false
            btnRegister.text = "Creating account..."

            lifecycleScope.launch {
                try {
                    val response = ApiClient.api.signUp(
                        SignUpRequest(
                            full_name = name, email = email, phone = phone, dob = dob,
                            gender = genders[spGender.selectedItemPosition].second,
                            account_type = if (rbWork.isChecked) "work" else "home",
                            meter_number = meter, password = password
                        )
                    )
                    if (response.isSuccessful) {
                        val next = Intent(this@RegisterActivity, VerifyActivity::class.java)
                        next.putExtra("EMAIL", response.body()?.email ?: email.lowercase())
                        startActivity(next)
                        finish()
                    } else {
                        toast(response.errorText("Could not create the account"))
                    }
                } catch (e: Exception) {
                    toast("Cannot reach the server. Check your internet connection.")
                } finally {
                    btnRegister.isEnabled = true
                    btnRegister.text = "Create account"
                }
            }
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}