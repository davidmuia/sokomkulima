from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect

app = Flask(__name__)
app.config['SECRET_KEY'] = 'rasnjvndshepnfhsfakhgri'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///agrimarket.db'
app.config['SESSION_COOKIE_HTTPONLY'] = True  # prevents JS from reading cookies

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


# --- DATABASE MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    phone = db.Column(db.String(20), unique=True)
    password = db.Column(db.String(255))
    role = db.Column(db.String(20))
    location = db.Column(db.String(100))


class Produce(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    name = db.Column(db.String(100))
    quantity = db.Column(db.Float)
    unit = db.Column(db.String(20))
    price = db.Column(db.Float)
    date_available = db.Column(db.String(50))
    delivery = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default='Active')
    farmer = db.relationship('User', backref='produces')


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    produce_id = db.Column(db.Integer, db.ForeignKey('produce.id'))
    qty_requested = db.Column(db.Float)
    status = db.Column(db.String(20), default='Pending')
    date_ordered = db.Column(db.DateTime, default=datetime.utcnow)
    buyer = db.relationship('User', backref='orders')
    produce = db.relationship('Produce')

KENYAN_COUNTIES = [
    'Nairobi', 'Mombasa', 'Nakuru', 'Kiambu', 'Machakos',
    'Kisumu', 'Uasin Gishu', 'Meru', 'Nyeri', 'Kakamega', 'Kajiado'
]
@app.context_processor
def inject_counties():
    return dict(counties=KENYAN_COUNTIES)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- ROUTES ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone')
        password = request.form.get('password')
        user = User.query.filter_by(phone=phone).first()

        # Security: Check hashed password
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('farmer_dash' if user.role == 'farmer' else 'marketplace'))
        flash('Invalid phone or password', 'error')
    return render_template('login.html')


@app.route('/register', methods=['POST'])
def register():
    name = request.form.get('name')
    phone = request.form.get('phone')
    password = request.form.get('password')
    role = request.form.get('role')
    location = request.form.get('location')

    if not User.query.filter_by(phone=phone).first():
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(name=name, phone=phone, password=hashed_pw, role=role, location=location)
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        return redirect(url_for('farmer_dash' if role == 'farmer' else 'marketplace'))
    flash('Phone number already registered', 'error')
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def farmer_dash():
    if current_user.role != 'farmer': return redirect(url_for('marketplace'))

    my_produce = Produce.query.filter_by(farmer_id=current_user.id).all()

    # Filtering Logic
    buyer_name = request.args.get('buyer_name', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    # Determine which tab should be active on load (default to 'produce')
    active_tab = request.args.get('tab', 'produce')

    query = Order.query.join(Produce).join(User, Order.buyer_id == User.id) \
        .filter(Produce.farmer_id == current_user.id)

    if buyer_name: query = query.filter(User.name.ilike(f'%{buyer_name}%'))
    if start_date: query = query.filter(Order.date_ordered >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        query = query.filter(Order.date_ordered <= end_dt)

    orders = query.order_by(Order.date_ordered.desc()).all()

    pending_orders = [o for o in orders if o.status == 'Pending']
    history_orders = [o for o in orders if o.status != 'Pending']

    # Quick Stats for the app UI
    stats = {
        'active_produce': len([p for p in my_produce if p.status == 'Active']),
        'pending_count': len(pending_orders),
        'total_sales': sum([o.qty_requested * o.produce.price for o in history_orders if o.status == 'Accepted'])
    }

    return render_template('farmer_dash.html',
                           produce=my_produce,
                           pending=pending_orders,
                           history=history_orders,
                           stats=stats,
                           active_tab=active_tab)

@app.route('/add_produce', methods=['POST'])
@login_required
def add_produce():
    new_produce = Produce(
        farmer_id=current_user.id,
        name=request.form.get('name'),
        quantity=request.form.get('quantity'),
        unit=request.form.get('unit'),
        price=request.form.get('price'),
        date_available=request.form.get('date_available'),
        delivery=True if request.form.get('delivery') else False
    )
    db.session.add(new_produce)
    db.session.commit()
    return redirect(url_for('farmer_dash'))


@app.route('/marketplace')
@login_required
def marketplace():
    if current_user.role != 'buyer': return redirect(url_for('farmer_dash'))

    search_q = request.args.get('q', '')
    loc_filter = request.args.get('location', '')

    # Only show Active stock where quantity is greater than 0
    query = Produce.query.join(User).filter(Produce.status == 'Active', Produce.quantity > 0)

    if search_q: query = query.filter(Produce.name.ilike(f'%{search_q}%'))
    if loc_filter: query = query.filter(User.location == loc_filter)

    items = query.all()

    my_active_orders = Order.query.filter(
        Order.buyer_id == current_user.id,
        Order.status.in_(['Pending', 'Accepted'])
    ).all()

    requested_items = {}
    for order in my_active_orders:
        if order.produce_id in requested_items:
            requested_items[order.produce_id] += order.qty_requested
        else:
            requested_items[order.produce_id] = order.qty_requested

    return render_template('marketplace.html', items=items, requested_items=requested_items)


@app.route('/buyer_orders')
@login_required
def buyer_orders():
    if current_user.role != 'buyer': return redirect(url_for('farmer_dash'))

    active_tab = request.args.get('tab', 'active')

    # History Filtering Logic
    search_q = request.args.get('q', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    query = Order.query.join(Produce).filter(Order.buyer_id == current_user.id)

    if search_q: query = query.filter(Produce.name.ilike(f'%{search_q}%'))
    if start_date: query = query.filter(Order.date_ordered >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        query = query.filter(Order.date_ordered <= end_dt)

    all_orders = query.order_by(Order.date_ordered.desc()).all()

    # Split into active (Pending/Accepted) and history (Rejected/Delivered)
    active_orders = [o for o in all_orders if o.status in ['Pending', 'Accepted']]
    history_orders = [o for o in all_orders if o.status not in ['Pending', 'Accepted']]

    return render_template('buyer_orders.html',
                           active_orders=active_orders,
                           history_orders=history_orders,
                           active_tab=active_tab)


@app.route('/order/<int:produce_id>', methods=['POST'])
@login_required
def place_order(produce_id):
    produce = Produce.query.get(produce_id)
    qty = float(request.form.get('qty'))

    # Backend check to prevent ordering more than available
    if qty > produce.quantity:
        flash(f'Cannot order more than the available {produce.quantity} {produce.unit}.', 'error')
        return redirect(url_for('marketplace'))

    new_order = Order(buyer_id=current_user.id, produce_id=produce_id, qty_requested=qty)
    db.session.add(new_order)
    db.session.commit()
    flash('Order placed successfully!', 'success')
    return redirect(url_for('marketplace'))


@app.route('/update_order/<int:order_id>', methods=['POST'])
@login_required
def update_order(order_id):
    status = request.form.get('status')
    order = Order.query.get(order_id)

    if order and order.produce.farmer_id == current_user.id:

        # 1. ACCEPT
        if status == 'Accepted' and order.status == 'Pending':
            order.status = status
            flash('Order Accepted! You can now contact the buyer', 'success')

        # 2. REJECT
        elif status == 'Rejected' and order.status == 'Pending':
            order.status = status
            flash('Order Rejected.', 'success')

        # 3. CANCEL:
        elif status == 'Cancelled' and order.status == 'Accepted':
            order.status = status
            flash('Order Cancelled', 'warning')

        # 4. SOLD (COMPLETED): Transaction successful.
        elif status == 'Completed' and order.status == 'Accepted':
            # Safety check: ensure stock is still available!
            if order.produce.quantity >= order.qty_requested:
                order.produce.quantity -= order.qty_requested
                order.status = status

                # Auto-hide produce if stock runs out
                if order.produce.quantity <= 0:
                    order.produce.status = 'Sold Out'

                flash('Produce marked as SOLD! ', 'success')
            else:
                flash(
                    f'Cannot mark as sold! You only have {order.produce.quantity} {order.produce.unit} left on the market.',
                    'error')
                return redirect(url_for('farmer_dash', tab='pending'))

        db.session.commit()

    return redirect(url_for('farmer_dash', tab='pending'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='127.0.0.1', port=8000)