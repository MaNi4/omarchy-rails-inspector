require "bundler/inline"

gemfile(true, quiet: true) do
  source "https://rubygems.org"
  gem "rails", "~> #{ENV.fetch("RAILS")}.0"
  gem "sqlite3", ENV.fetch("RAILS") < "7.1" ? "~> 1.4" : ">= 1.4"
  gem "logger"
  gem "ostruct"
  gem "benchmark"
  gem "bigdecimal"
  gem "mutex_m"
  gem "drb"
  gem "base64"
end

require "rails"
require "active_record/railtie"
require "action_controller/railtie"
require "action_view/railtie"

ENV["DATABASE_URL"] = "sqlite3::memory:"
FileUtils.mkdir_p("/app/log")
FileUtils.rm_f("/app/log/development.log")

class InspectorApp < Rails::Application
  config.root = "/app"
  config.eager_load = false
  config.consider_all_requests_local = true
  config.secret_key_base = "x" * 64
  config.hosts.clear
  config.logger = ActiveSupport::TaggedLogging.new(ActiveSupport::Logger.new("/app/log/development.log"))
  config.log_level = :debug
  config.active_record.verbose_query_logs = true
  config.active_support.deprecation = :log
  config.action_controller.action_on_unpermitted_parameters = :log
  config.active_record.action_on_strict_loading_violation = :log
  config.log_tags = [:request_id] unless ENV["TAGGED"].to_s.empty?
end

ActiveRecord::Base.establish_connection(adapter: "sqlite3", database: ":memory:")
Rails.application.initialize!
ActiveRecord::Base.logger = Rails.logger

ActiveRecord::Schema.define do
  create_table(:customers) { |t| t.string :name }
  create_table(:orders) { |t| t.integer :customer_id; t.integer :total }
end

class Customer < ActiveRecord::Base
  has_many :orders
end

class Order < ActiveRecord::Base
  belongs_to :customer
end

Rails.logger.silence do
  5.times { |i| Customer.create!(name: "c#{i}").orders.create!(total: i) }
end

DEPRECATOR = ActiveSupport::Deprecation.new("9.0", "Shop")
DEPRECATOR.behavior = :log

Rails.application.routes.draw do
  get "/orders" => "orders#index"
  get "/slow" => "orders#slow"
  get "/boom" => "orders#boom"
  get "/template" => "orders#template"
  get "/strict" => "orders#strict"
  get "/old" => "orders#old"
  post "/orders" => "orders#create"
end

class OrdersController < ActionController::Base
  skip_forgery_protection

  def index
    names = Order.all.map { |o| o.customer.name }
    render plain: names.join(",")
  end

  def slow
    Order.find_by_sql("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3000000) SELECT count(*) AS n FROM r")
    sleep 1.1
    render plain: "slow"
  end

  def boom
    Order.find(1).missing_method_here
  end

  def template
    render inline: "<%= @nothing.name %>"
  end

  def strict
    order = Order.strict_loading.first
    order.customer
    render plain: "strict"
  end

  def old
    DEPRECATOR.warn("old_thing is deprecated, use new_thing")
    render plain: "old"
  end

  def create
    params.require(:order).permit(:total)
    render plain: "ok"
  end
end

session = ActionDispatch::Integration::Session.new(Rails.application)
session.get "/orders"
session.get "/slow"
session.get "/boom"
session.get "/template"
session.get "/strict"
session.get "/old"
session.post "/orders", params: { order: { total: 1, admin: true } }
session.get "/missing"
Rails.logger.flush if Rails.logger.respond_to?(:flush)
warn "Rails #{Rails.version}: #{File.size("/app/log/development.log")} bytes"
