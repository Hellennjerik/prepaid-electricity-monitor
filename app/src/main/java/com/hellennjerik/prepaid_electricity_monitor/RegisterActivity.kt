package com.hellennjerik.prepaid_electricity_monitor

import android.app.DatePickerDialog
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.RadioButton
import android.widget.Spinner
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.Calendar

/**
 * Sign-up: name, email, phone, date of birth, gender, meter number and a password.
 * No email code. If the meter has no usage history yet, the server asks for three answers
 * (balance now, last top-up, days it lasted) and the screen shows those fields.
 */
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
        val etThreshold = findViewById<TextInputEditText>(R.id.etThreshold)
        val etBalance = findViewById<TextInputEditText>(R.id.etBalance)
        val etTopup = findViewById<TextInputEditText>(R.id.etTopup)
        val etDays = findViewById<TextInputEditText>(R.id.etDays)
        val historyBox = findViewById<View>(R.id.historyBox)
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
            val thresholdText = etThreshold.text.toString().trim()
            val threshold = if (thresholdText.isEmpty()) 100.0 else thresholdText.toDoubleOrNull()
            val balance = etBalance.text.toString().trim().toDoubleOrNull()
            val topup = etTopup.text.toString().trim().toDoubleOrNull()
            val days = etDays.text.toString().trim().toIntOrNull()
            val askedHistory = historyBox.visibility == View.VISIBLE

            when {
                threshold == null || threshold < 0 || threshold > 10000 ->
                    return@setOnClickListener toast("Alert level must be between KSh 0 and KSh 10,000")
                name.isEmpty() || email.isEmpty() || phone.isEmpty() || meter.isEmpty() || password.isEmpty() ->
                    return@setOnClickListener toast("Please fill in name, email, phone, meter number and password")
                password != etConfirm.text.toString() ->
                    return@setOnClickListener toast("The two passwords do not match")
                askedHistory && (balance == null || topup == null || days == null || days < 1 || days > 90) ->
                    return@setOnClickListener toast("Please answer the three usage questions (days must be 1 to 90)")
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
                            meter_number = meter, password = password, alert_threshold = threshold ?: 100.0,
                            current_balance = if (askedHistory) balance else null,
                            last_topup_ksh = if (askedHistory) topup else null,
                            last_topup_days = if (askedHistory) days else null
                        )
                    )
                    val token = response.body()?.token
                    val user = response.body()?.user
                    if (response.isSuccessful && token != null && user != null) {
                        SessionStore.save(token, user)
                        SessionStore.currentMeter = meter
                        toast("Welcome, ${user.full_name}!")
                        startActivity(Intent(this@RegisterActivity, DashboardActivity::class.java)
                            .putExtra("METER_NUMBER", meter)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
                        finish()
                    } else if (response.code() == 422 && needsHistory(response.errorBody()?.string())) {
                        // A brand-new meter: ask for the three history answers, then press the button again
                        historyBox.visibility = View.VISIBLE
                        toast("This meter is new. Please answer the three usage questions, then tap Create account again.")
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

    private fun needsHistory(body: String?): Boolean =
        try { JSONObject(body ?: "").optBoolean("needs_history") } catch (e: Exception) { false }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}