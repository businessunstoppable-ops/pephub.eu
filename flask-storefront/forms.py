# forms.py

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, IntegerField
from wtforms.validators import DataRequired, Email, Length, NumberRange

class AddToCartForm(FlaskForm):
    quantity = IntegerField('Quantity', 
                           validators=[DataRequired(), NumberRange(min=1, max=99)],
                           default=1)
    submit = SubmitField('Add to Cart')

class CheckoutForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email Address', validators=[DataRequired(), Email()])
    submit = SubmitField('Complete Purchase')